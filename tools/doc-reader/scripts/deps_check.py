#!/usr/bin/env python
"""doc-reader 依赖检查：仅 PDF 需要 pdfplumber，DOCX / XLSX 零依赖。

供 /ea setup 调用，也供 doc_reader.py 复用检测逻辑。

用法:
  py .../tools/doc-reader/scripts/deps_check.py --detect [--json]
  py .../tools/doc-reader/scripts/deps_check.py --install [--yes]

返回码: 0=PDF 可用 / 1=缺 pdfplumber（DOCX / XLSX 仍可用）
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PIP_DEPS: dict[str, tuple[str, str]] = {
    "import pdfplumber": ("pdfplumber", "pdfplumber"),
}


def _import_ok(import_stmt: str) -> bool:
    try:
        exec(import_stmt, {})
        return True
    except ImportError:
        return False


def requirements_path() -> Path:
    return Path(__file__).resolve().parent.parent / "requirements.txt"


def detect() -> dict:
    pip = {label: _import_ok(stmt) for stmt, (label, _pkg) in PIP_DEPS.items()}
    return {
        "pip": pip,
        "pdf_ready": pip.get("pdfplumber", False),
        # DOCX / XLSX 只用标准库，永远就绪
        "docx_ready": True,
        "xlsx_ready": True,
    }


def print_report(d: dict) -> None:
    print("📄 EA-SKILL 文档识别依赖检查\n")
    for label, ok in d["pip"].items():
        print(f"  {'✅' if ok else '⬜'} pip: {label}"
              + ("" if ok else "   （PDF 解析需要）"))
    print("  ✅ 标准库 zipfile+xml: DOCX / XLSX 零依赖就绪")
    print("\n汇总: "
          + ("/ea doc PDF 就绪 ✅" if d["pdf_ready"] else "/ea doc PDF 缺 pdfplumber ⬜")
          + " | DOCX/XLSX 就绪 ✅")


def do_install(yes: bool = False) -> int:
    req = requirements_path()
    if not req.is_file():
        print(f"❌ 找不到 {req}", file=sys.stderr)
        return 1
    missing = [pkg for stmt, (_l, pkg) in PIP_DEPS.items() if not _import_ok(stmt)]
    if not missing:
        print("✅ 依赖已就绪，无需安装")
        return 0
    print("将安装:", ", ".join(missing))
    if not yes:
        ans = input("是否继续？[y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("已取消（DOCX / XLSX 仍可正常使用）")
            return 1
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(req)]
    print("$", " ".join(cmd))
    return subprocess.call(cmd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--detect", action="store_true", help="检测依赖（默认）")
    parser.add_argument("--install", action="store_true", help="安装缺失 pip 依赖")
    parser.add_argument("--yes", "-y", action="store_true", help="--install 不询问")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    if args.install:
        return do_install(yes=args.yes)

    d = detect()
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        print_report(d)
    return 0 if d["pdf_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
