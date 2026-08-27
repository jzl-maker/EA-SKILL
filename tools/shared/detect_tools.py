#!/usr/bin/env python
"""自动探测嵌入式开发工具路径并注册到 tool_config。"""

from __future__ import annotations

import io
import os
import shutil
import sys
from pathlib import Path

try:
    import winreg
except ImportError:  # 非 Windows
    winreg = None

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

# 本脚本位于 ea-skill/tools/shared/，tool_config.py 同目录
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))
from tool_config import set_tool_path, list_tools

# ── 探测策略 ──────────────────────────────────────────────

TOOL_CHECKS: dict[str, list[str | Path]] = {
    "uv4": [
        # PATH 查找
        "UV4.exe",
        # Keil MDK 常见安装路径
        "C:/Keil_v5/UV4/UV4.exe",
        "C:/Keil/UV4/UV4.exe",
        "C:/Program Files/Keil/UV4/UV4.exe",
        "C:/Program Files (x86)/Keil/UV4/UV4.exe",
        "C:/Program Files/MDK/UV4/UV4.exe",
        "C:/Program Files (x86)/MDK/UV4/UV4.exe",
    ],
    "openocd": [
        "openocd",
        "openocd.exe",
        # xpack 常见路径
        str(Path.home() / "AppData/Local/xpack-openocd/*/bin/openocd.exe"),
        "C:/Program Files/xpack-openocd/*/bin/openocd.exe",
        "C:/Program Files (x86)/xpack-openocd/*/bin/openocd.exe",
        "C:/OpenOCD*/bin/openocd.exe",
        "D:/OpenOCD*/bin/openocd.exe",
        # xpack 两层结构: D:/OpenOCD/xpack-openocd-0.12.0-7/bin/openocd.exe
        "D:/OpenOCD*/xpack-openocd-*/bin/openocd.exe",
        "C:/OpenOCD*/xpack-openocd-*/bin/openocd.exe",
        str(Path.home() / "AppData/Local/xpack-openocd/*/bin/openocd.exe"),
    ],
    "jlink": [
        "JLink.exe",
        "JLink",
        "C:/Program Files/SEGGER/JLink*/JLink.exe",
        "C:/Program Files (x86)/SEGGER/JLink*/JLink.exe",
    ],
    "logic2": [
        "Logic.exe",
        str(Path.home() / "AppData/Local/Programs/Saleae/Logic 2/Logic.exe"),
        str(Path.home() / "AppData/Local/Saleae/Logic 2/Logic.exe"),
        "C:/Program Files/Saleae/Logic 2/Logic.exe",
        "C:/Program Files (x86)/Saleae/Logic 2/Logic.exe",
    ],
}

# 额外检查的环境变量位置
ENV_HINTS: dict[str, list[str]] = {
    "uv4": ["MDK_PATH", "KEIL_PATH", "UV4_PATH"],
    "openocd": ["OPENOCD_PATH", "OPENOCD_HOME"],
    "jlink": ["JLINK_PATH", "SEGGER_PATH", "JLink_PATH"],
    "logic2": ["LOGIC2_PATH", "SALEAE_PATH"],
}


def _reg_get(subkey: str, value: str) -> str | None:
    """遍历 HKLM/HKCU 读取注册表字符串值，失败返回 None。"""
    if winreg is None:
        return None
    for hkey in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hkey, subkey) as k:
                v, _ = winreg.QueryValueEx(k, value)
                return str(v)
        except OSError:
            continue
    return None


def _registry_find() -> dict[str, str]:
    """Windows 注册表探测：Keil UV4 / SEGGER J-Link 都会写入安装路径。"""
    result: dict[str, str] = {}

    # Keil MDK: HKLM\SOFTWARE\WOW6432Node\Keil\Products\MDK\Path = D:\...\Keil5\ARM
    keil_path = (
        _reg_get(r"SOFTWARE\WOW6432Node\Keil\Products\MDK", "Path")
        or _reg_get(r"SOFTWARE\Keil\Products\MDK", "Path")
    )
    if keil_path:
        base = Path(keil_path)
        # Path 通常指向 ...\Keil5\ARM，UV4.exe 在 ...\Keil5\UV4\
        for cand in (base.parent / "UV4", base / "UV4", base.parent.parent / "UV4"):
            exe = cand / "UV4.exe"
            if exe.is_file():
                result["uv4"] = os.path.normpath(str(exe))
                break

    # SEGGER J-Link: HKLM\SOFTWARE\SEGGER\J-Link\InstallPath = D:\...\JLink
    seg = (
        _reg_get(r"SOFTWARE\SEGGER\J-Link", "InstallPath")
        or _reg_get(r"SOFTWARE\WOW6432Node\SEGGER\J-Link", "InstallPath")
    )
    if seg:
        exe = Path(seg) / "JLink.exe"
        if exe.is_file():
            result["jlink"] = os.path.normpath(str(exe))

    return result


def find_tool(name: str, checks: list[str | Path]) -> str | None:
    """依次尝试 PATH 查找、路径校验、通配符扫描。"""
    for candidate in checks:
        candidate_str = str(candidate)
        # 先试 shutil.which（处理 PATH 和扩展名）
        found = shutil.which(candidate_str)
        if found:
            return os.path.normpath(found)

        # 通配符路径（含 *）
        if "*" in candidate_str:
            from glob import glob
            matches = sorted(glob(candidate_str))
            if matches:
                return os.path.normpath(matches[0])

        # 精确路径校验
        p = Path(candidate_str)
        if p.exists():
            return os.path.normpath(str(p.resolve()))

    # 环境变量探测
    for env_key in ENV_HINTS.get(name, []):
        env_val = os.environ.get(env_key)
        if env_val:
            env_path = Path(env_val)
            if env_path.is_dir():
                # 如果是目录，找里面的可执行文件
                exe_name = {
                    "uv4": "UV4.exe",
                    "openocd": "openocd.exe",
                    "jlink": "JLink.exe",
                    "logic2": "Logic.exe",
                }.get(name, name)
                for exe in env_path.rglob(exe_name):
                    return os.path.normpath(str(exe))
            elif env_path.exists():
                return os.path.normpath(str(env_path))

    return None


def main() -> int:
    print("🔧 EA-SKILL 工具路径自动探测\n")

    found_count = 0
    missing_count = 0

    # 注册表探测优先（Keil / SEGGER 安装必写注册表，能覆盖非标准安装路径）
    reg_found = _registry_find()
    if reg_found:
        print("  [注册表] 命中:")
        for name, path in reg_found.items():
            print(f"    {name}: {path}")

    for tool_name, checks in TOOL_CHECKS.items():
        path = reg_found.get(tool_name) or find_tool(tool_name, checks)
        if path:
            print(f"  ✅ {tool_name}: {path}")
            set_tool_path(tool_name, path, global_=True)
            found_count += 1
        else:
            print(f"  ⬜ {tool_name}: 未自动找到")
            missing_count += 1

    print(f"\n{'═' * 50}")
    print(f"结果: {found_count} 个工具已注册, {missing_count} 个需手动配置\n")

    if missing_count > 0:
        print("手动注册命令参考:")
        print(f'  python -c "')
        print(f'  import sys; sys.path.insert(0, r\'{_THIS_DIR}\')')
        print(f'  from tool_config import set_tool_path')
        print(f'  set_tool_path(\'<工具名>\', r\'<完整路径>\', global_=True)')
        print(f'  print(\'✅ 已注册\')')
        print(f'  "')

    print("\n当前已注册工具:")
    existing = list_tools()
    if existing:
        for name, info in existing.items():
            print(f"  {name}: {info['path']} ({info['source']})")
    else:
        print("  (无)")

    return 0 if found_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
