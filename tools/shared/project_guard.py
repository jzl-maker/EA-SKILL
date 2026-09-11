#!/usr/bin/env python
"""EA-SKILL 工程保护审计工具（project_guard）。

对保护区文件 + 关键源文件做 SHA-256 基线快照；改动检查 + 审批记录。

用法：
  --snapshot           对保护区 + 关键文件建立基线 → 写入 <STATE_DIR>/context.md 基线区
                       （同时把文件内容副本存到 <STATE_DIR>/baseline/，供 --check 出 diff）
  --check              比对当前哈希 vs 基线，输出变更清单 + **保护区文件的 unified diff**
  --check --diff       连非保护区（关键源文件）的 diff 一起出
  --approve <文件> <理由>  记录一次人工审批的保护区改动 → <STATE_DIR>/approvals.md

状态目录：.ea 优先、.em 兼容。

为什么要存内容副本：基线原先只有 sha256，只能报"某文件变了"，报不出**变了什么**。
而 record.md / verify.md 都要求"给用户看 diff 再确认"，非 git 工程里这个 diff 就无处可取
—— 审计就成了只报名字的空壳。副本与哈希放在一起，`--snapshot` 时一并落盘。
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import io
import shutil
import sys
from datetime import datetime
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
elif sys.stdout:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
elif sys.stderr:
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 保护区 glob 模式：startup、中断向量表、链接脚本、system 核心初始化
PROTECTED_GLOBS = (
    "**/startup_*.s",
    "**/startup_*.S",
    "**/*.sct",
    "**/*.ld",
    "**/*.icf",
    "**/system_*.c",
    "**/vector*.c",
    "**/vectors*.s",
)
# 关键源文件基线（可配置：/ea setup 后按 context 扩展）
KEY_GLOBS = ("**/*.c", "**/*.h")

# 基线内容副本目录（相对 <STATE_DIR>）
BASELINE_DIR = "baseline"
# 单文件超过这个大小就不存副本：多半是生成物/资源，不是要人眼审的源码
MAX_CONTENT_BYTES = 1 << 20
# 单文件 diff 最多打印多少行（超出截断，完整副本仍在 baseline/ 下）
MAX_DIFF_LINES = 80


def _state_dir(cwd: Path) -> Path:
    for d in (".ea", ".em"):
        p = cwd / d
        if p.is_dir():
            return p
    return cwd / ".ea"


def _sha256(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def snapshot(cwd: Path, state: Path) -> dict[str, str]:
    """扫描受保护 + 关键文件，返回 {相对路径: sha256}。"""
    base: dict[str, str] = {}
    for pat in (*PROTECTED_GLOBS, *KEY_GLOBS):
        for p in cwd.glob(pat):
            if ".ea" in p.parts or ".em" in p.parts:
                continue
            h = _sha256(p)
            if h:
                base[p.relative_to(cwd).as_posix()] = h
    return base


def _load_baseline(state: Path) -> dict[str, str]:
    path = state / "context.md"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    base: dict[str, str] = {}
    in_sec = False
    for line in text.splitlines():
        if line.strip().startswith("##") and "基线" in line or line.strip().startswith("##") and "baseline" in line:
            in_sec = True
            continue
        if line.strip().startswith("##") and in_sec:
            break
        if in_sec and "`" in line and "sha256:" in line:
            # 格式: - `path` sha256: hex
            rel, _, h = line.replace("`", "").partition(" sha256:")
            rel = rel.strip()
            if rel.startswith("-"):
                rel = rel[1:].strip()
            base[rel] = h.strip()
    return base


def _write_baseline(state: Path, base: dict[str, str]) -> None:
    ctx = state / "context.md"
    ctx.parent.mkdir(parents=True, exist_ok=True)
    text = ctx.read_text(encoding="utf-8", errors="replace") if ctx.is_file() else ""
    marker = "## 关键文件基线（project_guard）"
    if marker in text:
        head, _, tail = text.partition(marker)
        text = head + marker + "\n"
    else:
        text = text.rstrip() + "\n\n" + marker + "\n"
    lines = [text, f"- 快照时间: {datetime.now().isoformat(timespec='seconds')}\n"]
    for rel in sorted(base):
        lines.append(f"- `{rel}` sha256: {base[rel]}\n")
    ctx.write_text("".join(lines), encoding="utf-8")


def check(cwd: Path, state: Path) -> tuple[list[str], list[str], dict[str, str]]:
    """比对。返回 (已改保护区, 已改关键文件, 删除项)。"""
    baseline = _load_baseline(state)
    current = snapshot(cwd, state)
    changed_protected: list[str] = []
    changed_key: list[str] = []
    removed: dict[str, str] = {}

    all_paths = set(baseline) | set(current)
    for rel in sorted(all_paths):
        old = baseline.get(rel)
        new = current.get(rel)
        if old is None:
            continue  # 新文件不算基线差异（改动才报）
        if new is None:
            removed[rel] = old
            continue
        if old != new:
            target = changed_protected if _is_protected(rel) else changed_key
            target.append(rel)
    return changed_protected, changed_key, removed


def _baseline_dir(state: Path) -> Path:
    return state / BASELINE_DIR


def _read_text(path: Path) -> str | None:
    """按工程实际编码读文本。

    先 UTF-8 再 GBK：Keil 工程的 `.c/.h` 多为 GB2312（见 SKILL.md 红线 5），
    直接 UTF-8 读会整篇变成替换字符，diff 出来全是乱码、比不出真东西。
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def save_contents(cwd: Path, state: Path, base: dict[str, str]) -> tuple[int, list[str]]:
    """把基线里每个文件的内容副本存到 `<STATE_DIR>/baseline/`。返回 (已存数, 跳过的相对路径)。

    整个基线目录会在写之前清掉：**残留的旧副本会造成假 diff** —— 若某文件这次被跳过
    （如体积超限），而它的旧副本还在，`--check` 就会拿一份过期的"基线"去比，
    报出一堆根本不存在的差异。
    """
    dst_root = _baseline_dir(state)
    if dst_root.exists():
        shutil.rmtree(dst_root, ignore_errors=True)
    saved, skipped = 0, []
    for rel in sorted(base):
        src = cwd / rel
        try:
            if src.stat().st_size > MAX_CONTENT_BYTES:
                skipped.append(rel)
                continue
            data = src.read_bytes()
        except OSError:
            skipped.append(rel)
            continue
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        saved += 1
    return saved, skipped


def make_diff(cwd: Path, state: Path, rel: str, limit: int = MAX_DIFF_LINES) -> list[str]:
    """返回该文件的 unified diff 行；拿不到基线副本时返回一行说明（**不静默返回空**）。"""
    old_path = _baseline_dir(state) / rel
    if not old_path.is_file():
        return [f"  (无基线副本，无法出 diff —— 下次 `--snapshot` 后即可)"]
    old = _read_text(old_path)
    new = _read_text(cwd / rel)
    if old is None or new is None:
        return ["  (文件读取失败，无法出 diff)"]
    lines = list(difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm=""))
    if not lines:
        # 哈希变了但文本一致：多半是行尾/编码层面的差异，值得说破而不是留白
        return ["  (文本内容无差异，哈希差异可能来自行尾/BOM/编码)"]
    if len(lines) > limit:
        lines = lines[:limit] + [f"  … 其余 {len(lines) - limit} 行已截断"
                                 f"（完整基线副本: {old_path}）"]
    return lines


def _is_protected(rel: str) -> bool:
    p = Path(rel)
    for pat in PROTECTED_GLOBS:
        # match 对顶层文件的 `**/` 前缀模式不命中，需剥掉前缀再试
        if p.match(pat):
            return True
        if pat.startswith("**/") and p.match(pat[3:]):
            return True
    return False


def approve(cwd: Path, state: Path, rel: str, reason: str) -> Path:
    """记录一次人工审批的保护区改动。"""
    app = state / "approvals.md"
    app.parent.mkdir(parents=True, exist_ok=True)
    line = (
        f"- {datetime.now().isoformat(timespec='seconds')} | `{rel}` | {reason}"
        + ("\n" if not reason.endswith("\n") else "")
    )
    text = app.read_text(encoding="utf-8", errors="replace") if app.is_file() else ""
    if not text:
        text = "# 保护区改动审批记录\n\n"
    app.write_text(text + line, encoding="utf-8")
    return app


def load_approvals(state: Path) -> list[str]:
    app = state / "approvals.md"
    if not app.is_file():
        return []
    return [ln.split("|")[1].strip().strip("`")
            for ln in app.read_text(encoding="utf-8", errors="replace").splitlines()
            if "|" in ln]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", action="store_true", help="建立基线快照")
    parser.add_argument("--check", action="store_true", help="检查改动（含保护区 diff）")
    parser.add_argument("--diff", action="store_true",
                        help="--check 时连关键源文件的 diff 一起输出")
    parser.add_argument("--max-diffs", type=int, default=10,
                        help="--diff 最多打印几个文件的 diff（默认 10）")
    parser.add_argument("--approve", nargs=2, metavar=("FILE", "REASON"), help="审批保护区改动")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="工程根目录")
    args = parser.parse_args(argv)

    cwd = args.root.resolve()
    state = _state_dir(cwd)

    if args.approve:
        rel, reason = args.approve
        app = approve(cwd, state, rel, reason)
        print(f"✅ 已记录审批：`{rel}` → {app}")
        return 0

    if args.snapshot:
        base = snapshot(cwd, state)
        _write_baseline(state, base)
        saved, skipped = save_contents(cwd, state, base)
        print(f"📸 基线已写入 {state / 'context.md'}（{len(base)} 个文件）")
        print(f"   内容副本: {saved} 个 → {_baseline_dir(state)}")
        if skipped:
            print(f"   ⚠️ {len(skipped)} 个文件未存副本（体积超限或读取失败），"
                  f"其 diff 将不可用: {', '.join(skipped[:3])}"
                  f"{' …' if len(skipped) > 3 else ''}")
        return 0

    if args.check:
        prot, key, removed = check(cwd, state)
        approved = load_approvals(state)
        print("── 保护区改动 ──")
        if not prot:
            print("  无")
        for rel in prot:
            status = "✅ 已审批" if rel in approved else "⛔ 未审批"
            print(f"  {status} {rel}")
        print("── 关键文件改动 ──")
        if not key:
            print("  无")
        for rel in key:
            print(f"  ✏️  {rel}")
        if removed:
            print("── 已删除 ──")
            for rel in removed:
                print(f"  🗑️  {rel}")

        # 保护区 diff 默认就打：需要人工审批的就是这些文件，只给个文件名让人签"确认"，
        # 等于让用户在没看见改动的情况下批准改动。
        if prot:
            print(f"\n── 保护区 diff（{len(prot)} 个）──")
            for rel in prot:
                print(f"\n▌ {rel}")
                for line in make_diff(cwd, state, rel):
                    print(line)
        if key:
            if args.diff:
                shown = key[:args.max_diffs]
                print(f"\n── 关键文件 diff（{len(shown)}/{len(key)} 个）──")
                for rel in shown:
                    print(f"\n▌ {rel}")
                    for line in make_diff(cwd, state, rel):
                        print(line)
                if len(key) > len(shown):
                    print(f"\n… 其余 {len(key) - len(shown)} 个用 --max-diffs 放宽，"
                          f"或直接看 {_baseline_dir(state)}/ 下的副本")
            else:
                print(f"\nℹ️  关键文件改动了 {len(key)} 个，加 --diff 可看具体 diff")

        unapproved = [r for r in prot if r not in approved]
        if unapproved:
            print(f"\n⛔ {len(unapproved)} 个保护区文件改动未审批 → verify 阻塞")
            return 2
        if prot:
            print("\n✅ 保护区改动均有审批记录")
        print("\n✅ 检查完成")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
