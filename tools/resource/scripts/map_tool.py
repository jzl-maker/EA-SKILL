#!/usr/bin/env python
"""/ea map：Keil .map 文件解析工具。

功能：
  --objects   对象内存排行（Image component sizes：逐 .o 的 Code/RO/RW/ZI）
  --regions   内存分段分布（Execution Region：exec/load base、size、max、使用率）
  --symbols   符号排行 top N（变量 Data/Section + 函数 Code）；带过滤名则精确查地址映射
  --stack     栈/堆分配详情（STACK/HEAP Section + __initial_sp/__heap_base 地址）

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


def do_objects(data: dict, args) -> int:
    objs = data["objects"]
    if not objs:
        print("（无对象数据）")
        return 0
    ranked = sorted(objs, key=lambda o: -o["total"])
    top = ranked[: args.top]
    print(f"📦 对象内存排行 top{len(top)}（按 Code+RO+RW+ZI 总量降序）\n")
    print(f"  {'Code':>7s} {'RO':>7s} {'RW':>7s} {'ZI':>7s} {'总量':>7s}  Object")
    for o in top:
        print(f"  {o['code']:>7d} {o['ro']:>7d} {o['rw']:>7d} {o['zi']:>7d} "
              f"{o['total']:>7d}  {o['name']}")
    print(f"\n  共 {len(objs)} 个对象；ZI 合计 "
          f"{format_bytes(sum(o['zi'] for o in objs))}")
    return 0


def do_regions(data: dict, args) -> int:
    regions = data["regions"]
    if not regions:
        print("（无 Execution Region 数据）")
        return 0
    print("🗺️  内存分段分布\n")
    print(f"  {'Region':<18s} {'Exec base':>10s} {'Load base':>10s} "
          f"{'Size':>9s} {'Max':>9s} {'使用率':>7s}")
    for r in regions:
        pct = f"{r['size'] / r['max'] * 100:.1f}%" if r["max"] else "-"
        print(f"  {r['name']:<18s} 0x{r['exec_base']:08x} 0x{r['load_base']:08x} "
              f"{format_bytes(r['size']):>9s} {format_bytes(r['max']):>9s} {pct:>7s}")
    return 0


def do_symbols(data: dict, args) -> int:
    symbols = data["symbols"]
    if args.symbols:  # 精确查询：名字/地址过滤
        q = args.symbols.lower()
        hits = []
        for s in symbols:
            if q in s["name"].lower():
                hits.append(s)
        if not hits and q.startswith("0x"):
            addr = int(q, 16)
            hits = [s for s in symbols if s["value"] == addr]
        if not hits:
            print(f"（无匹配符号: {args.symbols}）")
            return 1
        print(f"🔍 符号匹配 {len(hits)} 条（{args.symbols}）\n")
        print(f"  {'Symbol':<32s} {'地址':>10s} {'Size':>8s} {'类型':<12s} Object")
        for s in sorted(hits, key=lambda x: -x["size"]):
            print(f"  {s['name']:<32s} 0x{s['value']:08x} {s['size']:>7d} "
                  f"{s['type']:<12s} {s['obj']}")
        return 0

    ranked = [s for s in symbols if s["size"] > 0]
    ranked.sort(key=lambda s: -s["size"])
    top = ranked[: args.top]
    print(f"📊 符号内存排行 top{len(top)}（size>0，按字节降序）\n")
    print(f"  {'Symbol':<32s} {'地址':>10s} {'Size':>8s} {'类型':<12s} Object")
    for s in top:
        print(f"  {s['name']:<32s} 0x{s['value']:08x} {s['size']:>7d} "
              f"{s['type']:<12s} {s['obj']}")
    return 0


def do_stack(data: dict, args) -> int:
    print("🧗  栈/堆详情\n")
    stack, heap = data["stack_bytes"], data["heap_bytes"]
    if stack is not None:
        print(f"  STACK 分配: {format_bytes(stack)}")
    else:
        print("  STACK: 未在符号表找到 Section")
    if heap is not None:
        print(f"  HEAP  分配: {format_bytes(heap)}")
    else:
        print("  HEAP : 未在符号表找到 Section")
    for name in ("__initial_sp", "__heap_base", "__heap_limit"):
        for s in data["symbols"]:
            if s["name"] == name:
                print(f"  {name:<15s} 0x{s['value']:08x}")
                break
    print("\n  ⚠️  栈分配量是 startup 定义值；运行期实际峰值请用 /ea debug 读 SP 测量")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Keil .map 文件解析：对象排行/分段分布/符号映射（/ea map）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  py map_tool.py --map build/xxx.map --objects\n"
            "  py map_tool.py --map build/xxx.map --regions\n"
            "  py map_tool.py --map build/xxx.map --symbols          # 排行\n"
            "  py map_tool.py --map build/xxx.map --symbols __initial_sp\n"
            "  py map_tool.py --map build/xxx.map --stack\n"
        ),
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--map", help="显式指定 .map 文件")
    src.add_argument("--project", help="工程目录（扫描最新 .map）")
    act = parser.add_mutually_exclusive_group()
    act.add_argument("--objects", action="store_true", help="对象内存排行（默认）")
    act.add_argument("--regions", action="store_true", help="内存分段分布")
    act.add_argument("--symbols", nargs="?", const="", metavar="FILTER",
                     help="符号排行；带名字/地址过滤则精确查询")
    act.add_argument("--stack", action="store_true", help="栈/堆详情")
    parser.add_argument("--top", type=int, default=20, help="排行条数（默认 20）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--dry-run", action="store_true", help="只打印流程不解析")
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_utf8()
    args = build_parser().parse_args(argv)
    if args.dry_run:
        src = args.map or (f"<{args.project} 扫描>" if args.project else "<当前目录扫描>")
        print(f"🔍 dry-run：解析 {src} → "
              f"{'对象排行' if args.objects or not args.regions else '分段分布' if args.regions else '符号'}")
        return 0

    map_file = resolve_map(args)
    data = parse_map(map_file)
    if args.json:
        emit_json(data)
        return 0
    print(f"📄 {map_file}\n")
    if args.regions:
        return do_regions(data, args)
    if args.symbols is not None:
        return do_symbols(data, args)
    if args.stack:
        return do_stack(data, args)
    return do_objects(data, args)


if __name__ == "__main__":
    sys.exit(main())
