#!/usr/bin/env python
"""/ea size：固件资源分析（Flash/RAM/栈/堆 + 超限预警）。

解析 Keil 链接器生成的 .map 文件（armcc v5 / armclang v6）：
  - Flash  = Total ROM Size（Code + RO Data + RW Data）
  - RAM    = Total RW Size（RW Data + ZI Data）
  - 容量   优先取 Execution Region 的 Max（链接脚本区域上限），--flash-kb/--ram-kb 可覆盖
  - 栈/堆  取 Global Symbols 里 STACK/HEAP Section 的分配量（startup 定义值）
            ⚠️ 这是分配量；运行期峰值请用 /ea debug 读 SP。

依赖：纯标准库（复用同目录 map_parser.py）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from map_parser import emit_json, ensure_utf8, find_map_file, format_bytes, parse_map


def resolve_map(args) -> Path:
    """定位 .map：--map 显式 > --project 扫描 > 当前目录扫描。"""
    if args.map:
        p = Path(args.map)
        if not p.is_file():
            sys.exit(f"❌ 找不到 .map 文件: {p}")
        return p
    p = find_map_file(args.project)
    if p is None:
        sys.exit("❌ 未找到 .map 文件（--map 指定，或 --project/当前目录需含编译产物）")
    return p


def _status(pct: float, warn: float, err: float) -> str:
    if pct >= err:
        return "❌ 超限"
    if pct >= warn:
        return "⚠️  偏高"
    return "✅"


def do_size(args) -> int:
    map_file = resolve_map(args)
    data = parse_map(map_file)
    totals = data["totals"]
    rom = totals.get("ROM")
    rw = totals.get("RW")

    flash_cap = args.flash_kb * 1024 if args.flash_kb else data["flash_capacity"]
    ram_cap = args.ram_kb * 1024 if args.ram_kb else data["ram_capacity"]

    report = {
        "source": str(map_file),
        "flash_bytes": rom,
        "flash_capacity": flash_cap,
        "flash_pct": round(rom / flash_cap * 100, 1) if rom is not None and flash_cap else None,
        "ram_bytes": rw,
        "ram_capacity": ram_cap,
        "ram_pct": round(rw / ram_cap * 100, 1) if rw is not None and ram_cap else None,
        "stack_bytes": data["stack_bytes"],
        "heap_bytes": data["heap_bytes"],
        "warn_pct": args.warn,
        "err_pct": args.err,
    }
    if args.json:
        emit_json(report)
        return 0

    print(f"📊 固件资源分析  {map_file}\n")
    if rom is not None:
        if not flash_cap:
            print("  Flash : 未配置容量，无法计算使用率（--flash-kb 指定）")
        else:
            print(f"  Flash : {format_bytes(rom):>9s} / {format_bytes(flash_cap):>9s}"
                  f"  ({report['flash_pct']}%)  {_status(report['flash_pct'], args.warn, args.err)}")
    if rw is not None:
        if not ram_cap:
            print("  RAM   : 未配置容量，无法计算使用率（--ram-kb 指定）")
        else:
            print(f"  RAM   : {format_bytes(rw):>9s} / {format_bytes(ram_cap):>9s}"
                  f"  ({report['ram_pct']}%)  {_status(report['ram_pct'], args.warn, args.err)}")
    stack, heap = data["stack_bytes"], data["heap_bytes"]
    if stack is not None:
        print(f"  栈    : {format_bytes(stack)} (startup 分配量；运行期峰值见 /ea debug)")
    if heap is not None:
        print(f"  堆    : {format_bytes(heap)}")

    over = [k for k in ("flash", "ram") if report[f"{k}_pct"] is not None
            and report[f"{k}_pct"] >= args.err]
    if over:
        print(f"\n  ❌ 超限预警触发: {'、'.join(over)} 使用率 ≥ {args.err}%")
        # 触发阈值按失败返回：verify / CI 里 `/ea size` 是当检查用的，超限必须能中断流程。
        # 打印了 ❌ 却 exit 0，调用方只能去解析输出文本 —— 脚本化编排就白做了。
        return 1
    if report["flash_pct"] is not None and report["ram_pct"] is not None:
        print(f"\n  ✅ 均在阈值内（warn {args.warn}% / err {args.err}%）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="固件资源分析：Flash/RAM/栈/堆 + 超限预警（/ea size）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  py size_tool.py --map build/xxx.map\n"
            "  py size_tool.py --project . --flash-kb 512 --ram-kb 144 --warn 85\n"
            "  py size_tool.py --map build/xxx.map --json\n"
        ),
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--map", help="显式指定 .map 文件")
    src.add_argument("--project", help="工程目录（扫描最新 .map）")
    parser.add_argument("--flash-kb", type=int, help="Flash 容量 KB（缺省用链接脚本 region Max）")
    parser.add_argument("--ram-kb", type=int, help="RAM 容量 KB（缺省用链接脚本 region Max）")
    # help 串里的百分号要写 %%：argparse 会对 help 做 `help % params`，
    # 裸 `%` 后跟全角括号会抛 ValueError: unsupported format character（`--help` 直接崩）
    parser.add_argument("--warn", type=float, default=90, help="预警阈值 %%（默认 90）")
    parser.add_argument("--err", type=float, default=95, help="超限阈值 %%（默认 95）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--dry-run", action="store_true", help="只打印流程不解析")
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_utf8()
    args = build_parser().parse_args(argv)
    if args.dry_run:
        src = args.map or (f"<{args.project} 扫描>" if args.project else "<当前目录扫描>")
        print(f"🔍 dry-run：解析 {src} → Flash/RAM 总量 → 对比容量 → 栈/堆 → 超限预警")
        return 0
    return do_size(args)


if __name__ == "__main__":
    sys.exit(main())
