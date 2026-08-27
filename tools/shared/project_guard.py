#!/usr/bin/env python
"""EA-SKILL 工程保护审计工具（project_guard）。

对保护区文件 + 关键源文件做 SHA-256 基线快照；改动检查 + 审批记录。

用法：
  --snapshot           对保护区 + 关键文件建立基线 → 写入 <STATE_DIR>/context.md 基线区
  --check              比对当前哈希 vs 基线，输出变更清单（是否涉保护区）
  --approve <文件> <理由>  记录一次人工审批的保护区改动 → <STATE_DIR>/approvals.md

状态目录：.ea 优先、.em 兼容。
"""

from __future__ import annotations

import argparse
import hashlib
import io
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
    parser.add_argument("--check", action="store_true", help="检查改动")
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
        print(f"📸 基线已写入 {state / 'context.md'}（{len(base)} 个文件）")
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
