"""把 EA-SKILL 嵌入式工具触发器幂等写入 ~/.claude/CLAUDE.md。

行为：
  - 模板不存在 → 报错退出（1）
  - ~/.claude/CLAUDE.md 不存在 → 创建并写入模板内容
  - 已存在但缺少 section header → 追加模板内容
  - 已存在且已有 section header：
      * 内容与模板一致 → 跳过（幂等）
      * 内容与模板不一致（如模板新增触发器行）→ 用模板内容替换该 section

由 /ea setup 步骤 5 调用。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 兼容 Windows GBK 控制台：强制 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SECTION_HEADER = "## EA-SKILL 嵌入式工具（动态加载）"


def _locate_template(start: Path) -> Path | None:
    """从脚本位置向上查找 templates/claude-md-snippet.md。"""
    cur = start.resolve()
    for parent in (cur, *cur.parents):
        candidate = parent / "templates" / "claude-md-snippet.md"
        if candidate.is_file():
            return candidate
    return None


def _skill_root(start: Path) -> Path | None:
    """从脚本位置向上查找包含 templates/ 的目录（即 EA-SKILL 根）。"""
    cur = start.resolve()
    for parent in (cur, *cur.parents):
        if (parent / "templates").is_dir():
            return parent
    return None


def _split_section(text: str) -> tuple[str, str, str]:
    """按 section header 把文本分成 (before, section, after)。

    找到 header 到下一个同级 `## ` 标题（或文件尾）之间的文本作为 section。
    """
    start = text.find(SECTION_HEADER)
    if start < 0:
        return text, "", ""
    after_start = len(text)
    for m in re.finditer(r"^##\s", text, re.MULTILINE):
        if m.start() > start + len(SECTION_HEADER):
            after_start = m.start()
            break
    return text[:start], text[start:after_start], text[after_start:]


def _render(template: Path) -> str:
    """读模板，并把 <SKILL> 换成真实安装路径。

    模板里写 <SKILL> 是为了让 skill 装到哪都能用（见 SKILL.md「路径约定」）；
    但写进 CLAUDE.md 的必须是可以直接执行的绝对路径，所以在落地时替换掉。
    """
    text = template.read_text(encoding="utf-8")
    root = _skill_root(template.parent)
    if root is not None:
        text = text.replace("<SKILL>", root.as_posix())
    return text


def register(target: Path, template: Path) -> str:
    """幂等写入。返回状态：created | appended | skipped | updated。"""
    snippet = _render(template)

    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(snippet, encoding="utf-8")
        return "created"

    existing = target.read_text(encoding="utf-8")
    if SECTION_HEADER not in existing:
        # 追加；前面留一个空行，避免与前一段粘连
        sep = "" if existing.endswith("\n") else "\n"
        target.write_text(existing + sep + "\n" + snippet, encoding="utf-8")
        return "appended"

    # header 已存在：内容与模板一致则跳过，不一致则用模板替换该 section
    before, old_section, after = _split_section(existing)
    if old_section.strip() == snippet.strip():
        return "skipped"
    target.write_text(before + snippet + after, encoding="utf-8")
    return "updated"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        type=Path,
        default=Path.home() / ".claude" / "CLAUDE.md",
        help="目标 CLAUDE.md 路径（默认 ~/.claude/CLAUDE.md）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="仅检查是否已注册，不写入（返回 0=已注册，1=未注册）",
    )
    args = parser.parse_args(argv)

    script_dir = Path(__file__).parent
    template = _locate_template(script_dir)
    if template is None:
        skill_root = _skill_root(script_dir)
        print(
            f"❌ 找不到模板 claude-md-snippet.md"
            f"（已在 {skill_root or script_dir} 上溯查找）",
            file=sys.stderr,
        )
        return 1

    if args.check:
        if args.target.is_file() and SECTION_HEADER in args.target.read_text(
            encoding="utf-8"
        ):
            print(f"✅ 已注册：{args.target}")
            return 0
        print(f"⚠️  未注册：{args.target}")
        return 1

    status = register(args.target, template)
    verb = {
        "created": "创建并写入",
        "appended": "追加 section",
        "skipped": "已存在且内容一致，跳过",
        "updated": "内容已更新（同步模板新触发器行）",
    }[status]
    print(f"✅ {verb}：{args.target}（模板：{template}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())