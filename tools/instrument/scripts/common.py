#!/usr/bin/env python
"""instrument 工具簇共享模块。

为两个仪器脚本（logic_analyzer.py / scope.py）与 deps_check.py 提供共用 harness：

- 强制 UTF-8 控制台（Windows GBK 兼容）
- tool_config bootstrap（向上找 shared/ 注入 sys.path，读/写工具路径）
- --json 结构化输出（emit_json）
- 可选依赖容错（require_module）
- <STATE_DIR>/captures/ 输出目录定位（.ea 优先 .em 兼容）
- Logic 2 可执行文件查找
- matplotlib Agg 后端强制（无 GUI 无头出图）
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# UTF-8 控制台
# ---------------------------------------------------------------------------


def reconfigure_console() -> None:
    """Windows GBK 控制台强制 UTF-8，避免中文/emoji 输出乱码或抛错。"""
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# 被 import 时立即生效，子脚本无需重复
reconfigure_console()

# ---------------------------------------------------------------------------
# tool_config bootstrap
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent


def _find_shared_dir(start: Path) -> Path | None:
    """从 start 向上找含 tool_config.py 的 shared/ 目录。"""
    for parent in (start, *start.parents):
        cand = parent / "shared"
        if (cand / "tool_config.py").exists():
            return cand
    return None


_SHARED_DIR = _find_shared_dir(_SCRIPT_DIR)
if _SHARED_DIR is not None:
    sys.path.insert(0, str(_SHARED_DIR))
try:
    from tool_config import get_tool_path, set_tool_path
except ImportError:
    get_tool_path = None  # type: ignore[assignment]
    set_tool_path = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# JSON 输出
# ---------------------------------------------------------------------------


def emit_json(obj: dict[str, Any]) -> None:
    """--json 模式：只输出一份 JSON 到 stdout，供 AI 解析。"""
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 输出目录
# ---------------------------------------------------------------------------


def state_dir(root: str | Path | None = None) -> Path | None:
    """定位 <STATE_DIR>：.ea/ 优先，.em/ 兼容；从 root 向上找最近的。"""
    base = Path(root) if root else Path.cwd()
    for parent in (base, *base.parents):
        for d in (".ea", ".em"):
            p = parent / d
            if p.is_dir():
                return p
    return None


def default_output_dir(prefix: str) -> Path:
    """<STATE_DIR>/captures/<prefix>_<ts>/；无状态目录时回退 ./captures/。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sd = state_dir()
    base = (sd / "captures") if sd else Path("captures")
    base.mkdir(parents=True, exist_ok=True)
    out = base / f"{prefix}_{ts}"
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# 可选依赖容错
# ---------------------------------------------------------------------------


def require_module(import_stmt: str, name: str, pip_hint: str) -> dict[str, Any] | None:
    """按 import 语句导入可选依赖；失败打印安装提示并返回 None。

    import_stmt 示例："from saleae import automation"
    name 示例："logic2-automation"（用于报错提示）
    """
    ns: dict[str, Any] = {}
    try:
        exec(import_stmt, ns)
    except ImportError as exc:
        print(f"❌ 未安装 {name}（{exc}），请先安装：", file=sys.stderr)
        print(f"   py -m pip install {pip_hint}", file=sys.stderr)
        return None
    return ns


def find_logic2_exe(explicit: str | None = None) -> str | None:
    """查找 Logic 2 可执行文件：显式参数 → tool_config → 常见路径 → PATH。"""
    if explicit:
        p = Path(explicit)
        return str(p) if p.exists() else None
    if get_tool_path:
        cfg = get_tool_path("logic2")
        if cfg and Path(cfg).exists():
            return str(Path(cfg))
    candidates = [
        str(Path.home() / "AppData/Local/Programs/Saleae/Logic 2/Logic.exe"),
        str(Path.home() / "AppData/Local/Saleae/Logic 2/Logic.exe"),
        "C:/Program Files/Saleae/Logic 2/Logic.exe",
        "C:/Program Files (x86)/Saleae/Logic 2/Logic.exe",
        "D:/Program Files/Saleae/Logic 2/Logic.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    found = shutil.which("Logic.exe")
    return found or None


def check_logic2_port(port: int) -> bool:
    """检测 Logic 2 自动化脚本端口是否可连（TCP connect 探测）。"""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            return s.connect_ex(("127.0.0.1", port)) == 0
        except OSError:
            return False


def ensure_matplotlib_agg() -> None:
    """强制 Agg 后端（无 GUI 无头出图），必须在 import pyplot 前调用。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
    except ImportError:
        pass


def parse_value(value: str) -> Any:
    """宽松数值解析：纯数字→int，含小数点→float，否则原样字符串。"""
    s = value.strip()
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def add_common_args(parser: Any) -> None:
    """给 argparse parser 追加 instrument 通用尾参：--dry-run / --json / -v。"""
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将执行的调用序列/SCPI 命令，不真正连接设备")
    parser.add_argument("--json", action="store_true",
                        help="结构化 JSON 输出（供 AI 解析）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试细节")
