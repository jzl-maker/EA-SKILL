#!/usr/bin/env python
"""git 可用性前置检查（/ea verify、/ea record 的 diff 环节依赖它）。

## 为什么需要单独一个检查

`verify.md` / `record.md` 都要求"列出改动 + 给用户看 diff"，做法是 `git status` +
`git diff --stat`。但这套命令有**两种静默失效**的情形，命令本身都返回 0，输出却是空的：

1. **仓库还没有任何提交**（`git rev-parse --verify HEAD` 失败）
   → `git diff --stat` 什么也不打印。看着像"本次没有任何改动"，其实改动全在。
   这是最危险的一种：**空 diff 会被当成"没改东西"**，开发记录就此失真。
2. **仓库根目录不是工程目录**（工程是某个 git 仓库的子目录，或有同级工程）
   → 在仓库根跑 `git status` / `git add .` 会把同级工程一起卷进来，
   提交里混入无关改动。工程目录下用 `--scope-only` 才能只看自己的改动。

本脚本把这些一次性查清楚，并给出**明确结论**（能用 git / 必须改用 project_guard 基线），
免得调用方拿到一句空输出就自己猜。

    py git_state.py                 # 检查当前目录
    py git_state.py --root <工程目录> --json
    py git_state.py --scope-only    # 只列工程目录内的改动文件（可直接喂给 record）

退出码：0=git diff 可用；1=不可用（改用 project_guard）；2=没装 git 或不是仓库。
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
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

GUARD_HINT = ("python ~/.claude/skills/EA-SKILL/tools/shared/project_guard.py "
              "--check --diff")


def _git(root: Path, *args: str) -> tuple[int, str]:
    """跑一条 git 命令，返回 (returncode, stdout+sderr)。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    except FileNotFoundError:
        return 127, "git not found"
    except subprocess.TimeoutExpired:
        return 124, "git 命令超时"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def inspect(root: Path) -> dict:
    """收集 git 状态。所有字段都可能是 None/空 —— 调用方须按 rc 判断，别假定有值。"""
    info: dict = {"root": str(root), "git_available": True, "is_repo": False,
                  "toplevel": None, "toplevel_is_root": None, "has_commit": None,
                  "changed_in_scope": [], "changed_out_of_scope": [],
                  "problems": [], "usable_for_diff": False}

    rc, out = _git(root, "rev-parse", "--show-toplevel")
    if rc == 127:
        info["git_available"] = False
        info["problems"].append("未找到 git 可执行文件")
        return info
    if rc != 0:
        info["problems"].append("当前目录不是 git 仓库（或不在任何仓库内）")
        return info

    info["is_repo"] = True
    toplevel = Path(out.strip().splitlines()[0]).resolve()
    info["toplevel"] = str(toplevel)
    info["toplevel_is_root"] = (toplevel == root)

    rc_head, _ = _git(root, "rev-parse", "--verify", "HEAD")
    info["has_commit"] = (rc_head == 0)

    # 改动清单：用 -z 分隔避免中文/空格路径被引号转义；--porcelain 是稳定接口
    rc_st, st = _git(root, "status", "--porcelain", "-z", "--untracked-files=all")
    if rc_st == 0:
        in_scope, out_scope = [], []
        for entry in st.split("\0"):
            if len(entry) < 4:
                continue
            path = entry[3:]
            if path.startswith('"') and path.endswith('"'):
                path = path[1:-1]
            try:
                full = (toplevel / path).resolve()
            except OSError:
                continue
            try:
                full.relative_to(root)
                in_scope.append(path)
            except ValueError:
                out_scope.append(path)
        info["changed_in_scope"] = in_scope
        info["changed_out_of_scope"] = out_scope

    # ---- 结论 ----
    if not info["has_commit"]:
        info["problems"].append(
            "仓库还没有任何提交 → `git diff --stat` 恒为空，"
            "会被误读成「本次没有改动」")
    if not info["toplevel_is_root"]:
        info["problems"].append(
            f"仓库根目录是 {toplevel}，**不是**工程目录 {root} → "
            f"在仓库根跑 `git add .`/`git status` 会带上同级工程")
    if info["changed_out_of_scope"]:
        info["problems"].append(
            f"{len(info['changed_out_of_scope'])} 个改动在本工程之外"
            f"（同级工程），提交时别一把 `git add .`")

    info["usable_for_diff"] = bool(info["has_commit"] and info["toplevel_is_root"])
    return info


def report(info: dict, scope_only: bool = False) -> int:
    if not info["git_available"]:
        print("❌ 未找到 git 可执行文件")
        print(f"   → diff 环节改用基线比对: {GUARD_HINT}")
        return 2
    if not info["is_repo"]:
        print(f"ℹ️  {info['root']} 不是 git 仓库")
        print("   → 非 git 工程，改动清单/diff 走基线比对:")
        print(f"     {GUARD_HINT}")
        return 2

    print(f"📁 git 仓库根: {info['toplevel']}")
    print(f"   工程目录  : {info['root']}")
    print(f"   有提交    : {'是' if info['has_commit'] else '否'}")
    print(f"   本工程内改动: {len(info['changed_in_scope'])} 个")
    if info["changed_out_of_scope"]:
        print(f"   工程外改动  : {len(info['changed_out_of_scope'])} 个"
              f"（同级工程，勿一起提交）")

    if scope_only:
        print("\n── 本工程内的改动文件 ──")
        if not info["changed_in_scope"]:
            print("  (无)")
        for p in info["changed_in_scope"]:
            print(f"  {p}")

    if info["problems"]:
        print("\n⚠️  git diff 环节不可靠:")
        for p in info["problems"]:
            print(f"   - {p}")

    if info["usable_for_diff"]:
        print("\n✅ git diff 可用（仓库根=工程目录，且有提交）")
        return 0

    print("\n❌ 不要用 `git diff` 作为改动清单 —— 它会静默给出空/错的答案。")
    print(f"   → 改用基线比对: {GUARD_HINT}")
    print("   → 首次使用需先建基线: project_guard.py --snapshot")
    if not info["toplevel_is_root"]:
        print("   → 确需用 git 时: 始终带**工程内相对路径**"
              "（如 `git status -- .`），不要 `git add .` / `git add -A`")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="git 可用性前置检查（verify/record 的 diff 环节依赖）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""退出码: 0=git diff 可用  1=不可用（改用基线比对）  2=无 git/非仓库

不可用时的替代方案:
  {GUARD_HINT}
""")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="工程根目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--scope-only", action="store_true",
                        help="额外列出本工程目录内的改动文件")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if not root.is_dir():
        print(f"❌ 目录不存在: {root}")
        return 2

    info = inspect(root)
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0 if info["usable_for_diff"] else 1
    return report(info, args.scope_only)


if __name__ == "__main__":
    sys.exit(main())
