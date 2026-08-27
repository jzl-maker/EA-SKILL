#!/usr/bin/env python
"""instrument 依赖检查：pip 包 + Logic 2 软件/端口 + Rigol VISA。

供 /ea setup 调用，也供 logic_analyzer.py / scope.py 的 --detect 复用检测逻辑。

用法:
  py .../tools/instrument/scripts/deps_check.py --detect [--json]
  py .../tools/instrument/scripts/deps_check.py --install
  py .../tools/instrument/scripts/deps_check.py --install --yes   # 不询问直接装

返回码: 0=依赖齐全 / 1=有缺失（pip 包缺失时）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from common import check_logic2_port, emit_json, find_logic2_exe, reconfigure_console

# pip 依赖清单：{import 检查用的导入语句: (显示名, pip 包名)}
PIP_DEPS: dict[str, tuple[str, str]] = {
    "from saleae import automation": ("logic2-automation", "logic2-automation"),
    "import pyvisa": ("pyvisa", "pyvisa"),
    "import pyvisa_py": ("pyvisa-py", "pyvisa-py"),
    "import numpy": ("numpy", "numpy"),
    "import matplotlib": ("matplotlib", "matplotlib"),
}

# 两个脚本各自必需的子集（供 --check-for 指定）
SCRIPT_DEPS = {
    "la": ["from saleae import automation", "import numpy", "import matplotlib"],
    "scope": ["import pyvisa", "import pyvisa_py", "import numpy", "import matplotlib"],
}


def _import_ok(import_stmt: str) -> bool:
    try:
        ns: dict = {}
        exec(import_stmt, ns)
        return True
    except ImportError:
        return False


def check_pip() -> dict[str, bool]:
    """返回 {显示名: 是否已安装}。"""
    result: dict[str, bool] = {}
    for import_stmt, (label, _pkg) in PIP_DEPS.items():
        result[label] = _import_ok(import_stmt)
    return result


def requirements_path() -> Path:
    """requirements.txt 位于 scripts/ 上一级。"""
    return Path(__file__).resolve().parent.parent / "requirements.txt"


def check_visa_resources() -> list[str] | None:
    """有 pyvisa 时返回 VISA 资源列表；无 pyvisa 返回 None。
    系统 VISA（NI-VISA/RIGOL IVI）优先——USB-TMC 需系统后端；无资源回退 @py（网口场景）。"""
    if not _import_ok("import pyvisa"):
        return None
    try:
        import pyvisa

        rm = pyvisa.ResourceManager()  # 系统 VISA
        res = list(rm.list_resources())
        if res:
            return res
        return list(pyvisa.ResourceManager("@py").list_resources())
    except Exception as exc:  # noqa: BLE001 - 探测阶段任何异常都算资源不可用
        return [f"<error: {exc}>"]


def detect() -> dict:
    """汇总检测结果。"""
    pip = check_pip()
    la_ok = all(_import_ok(s) for s in SCRIPT_DEPS["la"])
    scope_ok = all(_import_ok(s) for s in SCRIPT_DEPS["scope"])

    logic2_exe = find_logic2_exe()
    port_open = check_logic2_port(10430) if logic2_exe or la_ok else None
    resources = check_visa_resources() if scope_ok else None

    return {
        "pip": pip,
        "la_ready": la_ok,
        "scope_ready": scope_ok,
        "logic2_exe": logic2_exe,
        "logic2_port_open": port_open,
        "visa_resources": resources,
    }


def print_report(d: dict) -> None:
    print("🔬 EA-SKILL 仪器依赖检查\n")
    pip = d["pip"]
    for label, ok in pip.items():
        print(f"  {'✅' if ok else '⬜'} pip: {label}")
    print()
    exe = d.get("logic2_exe")
    if exe:
        print(f"  ✅ Logic 2 软件: {exe}")
        port = d.get("logic2_port_open")
        if port is True:
            print("  ✅ 自动化脚本端口 10430: 已开启")
        elif port is False:
            print("  ⬜ 自动化脚本端口 10430: 未开启（打开 Logic 2 → 设置 → 开启脚本服务器）")
        else:
            print("  ⬜ 自动化脚本端口 10430: 未检测")
    else:
        print("  ⬜ Logic 2 软件: 未找到（需从 Saleae 官网安装，winget install Saleae.Logic2）")

    resources = d.get("visa_resources")
    if resources is None:
        print("  ⬜ VISA 资源: pyvisa 未装，跳过")
    elif not resources:
        print("  ⬜ VISA 资源: 无设备（示波器未插 / 未装 USB-TMC 驱动，可 Zadig 装 WinUSB 或用系统 VISA）")
    else:
        print(f"  ✅ VISA 资源: {len(resources)} 个")
        for r in resources[:8]:
            print(f"      {r}")

    print("\n汇总: "
          + ("/ea la 就绪 ✅" if d["la_ready"] else "/ea la 缺依赖 ⬜")
          + " | "
          + ("/ea scope 就绪 ✅" if d["scope_ready"] else "/ea scope 缺依赖 ⬜"))


def do_install(yes: bool = False) -> int:
    """安装缺失的 pip 依赖（重依赖，默认询问）。"""
    req = requirements_path()
    if not req.is_file():
        print(f"❌ 找不到 {req}", file=sys.stderr)
        return 1
    missing = [pkg for _stmt, (_label, pkg) in PIP_DEPS.items()
               if not _import_ok(_stmt)]
    if not missing:
        print("✅ 所有 pip 依赖已就绪，无需安装")
        return 0
    print("将安装缺失依赖:", ", ".join(missing))
    if not yes:
        ans = input("是否继续？[y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("已取消")
            return 1
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(req)]
    print("$", " ".join(cmd))
    return subprocess.call(cmd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--detect", action="store_true", help="检测依赖与后端（默认）")
    parser.add_argument("--install", action="store_true", help="安装缺失 pip 依赖")
    parser.add_argument("--yes", "-y", action="store_true", help="--install 不询问")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    if args.install:
        return do_install(yes=args.yes)

    d = detect()
    if args.json:
        emit_json(d)
    else:
        print_report(d)
    return 0 if (d["la_ready"] and d["scope_ready"]) else 1


if __name__ == "__main__":
    sys.exit(main())
