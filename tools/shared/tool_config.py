"""轻量工具路径持久化配置层。

提供 JSON 配置文件的读写，支持工作区级（.ea_skill.json）和全局级
（%APPDATA%/ea_skill/config.json）两层配置，工作区覆盖全局。

解析优先级（由各脚本自行实现）：
  CLI 参数 → 配置文件 → 环境变量 → 硬编码路径 → PATH

也可以直接当命令行用（此前无参运行**一声不吭**，只能靠 `-c "import ..."` 手搓，
排查"工具到底注册到哪了"时很别扭）：

    py tool_config.py list                 # 列出已注册工具（含来源与最终生效项）
    py tool_config.py get <工具名>
    py tool_config.py set <工具名> <路径> [--global]
    py tool_config.py remove <工具名> [--global]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

CONFIG_FILENAME = ".ea_skill.json"


def user_config_path() -> Path:
    """返回全局配置文件路径（XDG / APPDATA）。"""
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "ea_skill" / "config.json"
    # XDG_CONFIG_HOME 或 ~/.config
    base = os.environ.get("XDG_CONFIG_HOME", "")
    if not base:
        base = str(Path.home() / ".config")
    return Path(base) / "ea_skill" / "config.json"


def workspace_config_path(workspace: str | Path | None = None) -> Path:
    """返回工作区级配置文件路径。"""
    ws = Path(workspace) if workspace else Path.cwd()
    return ws / CONFIG_FILENAME


def load_config(path: Path) -> dict[str, Any]:
    """读取 JSON 配置文件，文件不存在或格式错误时返回空字典。"""
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(path: Path, data: dict[str, Any]) -> None:
    """将配置写入 JSON 文件，自动创建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def get_tool_path(
    tool_name: str,
    workspace: str | Path | None = None,
) -> str | None:
    """合并读取工具路径（工作区优先于全局）。"""
    # 工作区级
    ws_cfg = load_config(workspace_config_path(workspace))
    ws_path = ws_cfg.get("tools", {}).get(tool_name)
    if ws_path:
        return ws_path

    # 全局级
    global_cfg = load_config(user_config_path())
    return global_cfg.get("tools", {}).get(tool_name)


def set_tool_path(
    tool_name: str,
    tool_path: str,
    workspace: str | Path | None = None,
    global_: bool = False,
) -> Path:
    """写入工具路径到指定级别的配置文件，返回写入的文件路径。"""
    cfg_path = user_config_path() if global_ else workspace_config_path(workspace)
    data = load_config(cfg_path)
    tools = data.setdefault("tools", {})
    tools[tool_name] = tool_path
    save_config(cfg_path, data)
    return cfg_path


def remove_tool_path(
    tool_name: str,
    workspace: str | Path | None = None,
    global_: bool = False,
) -> bool:
    """从配置中删除工具路径，返回是否实际删除了条目。"""
    cfg_path = user_config_path() if global_ else workspace_config_path(workspace)
    data = load_config(cfg_path)
    tools = data.get("tools", {})
    if tool_name not in tools:
        return False
    del tools[tool_name]
    save_config(cfg_path, data)
    return True


def list_tools(
    workspace: str | Path | None = None,
) -> dict[str, dict[str, str]]:
    """列出所有已配置的工具，返回 {tool_name: {"path": ..., "source": ...}}。"""
    result: dict[str, dict[str, str]] = {}

    global_cfg = load_config(user_config_path())
    for name, path in global_cfg.get("tools", {}).items():
        result[name] = {"path": path, "source": "global"}

    ws_cfg = load_config(workspace_config_path(workspace))
    for name, path in ws_cfg.get("tools", {}).items():
        result[name] = {"path": path, "source": "workspace"}

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_list(args: argparse.Namespace) -> int:
    tools = list_tools(args.workspace)
    if args.json:
        print(json.dumps(tools, ensure_ascii=False, indent=2))
        return 0
    if not tools:
        print("(无已注册工具)")
        print(f"  全局: {user_config_path()}")
        print(f"  工作区: {workspace_config_path(args.workspace)}")
        return 0
    print(f"{'工具':<12} {'来源':<10} 路径")
    for name, info in sorted(tools.items()):
        print(f"{name:<12} {info['source']:<10} {info['path']}")
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    path = get_tool_path(args.tool, args.workspace)
    if not path:
        print(f"❌ 未注册: {args.tool}")
        return 1
    source = list_tools(args.workspace).get(args.tool, {}).get("source", "?")
    print(f"{args.tool}: {path} ({source})")
    return 0


def _cmd_set(args: argparse.Namespace) -> int:
    p = Path(args.path)
    resolved = p.resolve()
    if not p.is_absolute():
        # 相对路径存进配置后，换个工作目录就失效 —— 落盘的一律转绝对路径
        print(f"⚠️ 相对路径已转为绝对路径: {resolved}")
    cfg = set_tool_path(args.tool, str(resolved), args.workspace, global_=args.global_)
    print(f"✅ 已注册 {args.tool} → {resolved}")
    print(f"   写入: {cfg}")
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    if remove_tool_path(args.tool, args.workspace, global_=args.global_):
        print(f"✅ 已移除 {args.tool}")
        return 0
    level = "全局" if args.global_ else "工作区"
    print(f"❌ {level}配置中没有 {args.tool}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tool_config.py",
        description="EA-SKILL 工具路径配置（工作区 .ea_skill.json 覆盖全局 config.json）",
        epilog=f"""全局配置: {user_config_path()}
工作区配置: .ea_skill.json（工作目录下，可用 --workspace 指定）""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--workspace", help="工作区目录（默认当前目录）")

    p_list = sub.add_parser("list", help="列出已注册工具")
    add_common(p_list)
    p_list.add_argument("--json", action="store_true", help="输出 JSON")
    p_list.set_defaults(func=_cmd_list)

    p_get = sub.add_parser("get", help="查询单个工具路径")
    p_get.add_argument("tool")
    add_common(p_get)
    p_get.set_defaults(func=_cmd_get)

    p_set = sub.add_parser("set", help="注册工具路径")
    p_set.add_argument("tool")
    p_set.add_argument("path")
    p_set.add_argument("--global", dest="global_", action="store_true",
                       help="写入全局配置（默认只写工作区）")
    add_common(p_set)
    p_set.set_defaults(func=_cmd_set)

    p_rm = sub.add_parser("remove", help="移除工具路径")
    p_rm.add_argument("tool")
    p_rm.add_argument("--global", dest="global_", action="store_true",
                      help="从全局配置移除（默认只动工作区）")
    add_common(p_rm)
    p_rm.set_defaults(func=_cmd_remove)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raise SystemExit(main())
