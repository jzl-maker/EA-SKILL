#!/usr/bin/env python
"""SVD 寄存器解析工具（/ea svd）。

把晦涩的寄存器地址/bit 位翻译成看得懂的外设寄存器名 + 位域含义，并支持读当前值：

- --detect            自动发现 SVD 文件（Keil pack 目录，按芯片名匹配）
- --periph GPIOA      列出外设下所有寄存器（地址/偏移/大小）
- --reg GPIOA MODER   寄存器详情：地址/位域/枚举定义
- --read GPIOA MODER  读取当前值（--backend jlink|openocd）+ 逐位域解码当前状态
- --find <关键词>      模糊搜索外设/寄存器名

纯标准库（xml.etree + subprocess + socket），无第三方依赖。
SVD 自动发现来源：tool_config 的 uv4 路径 → Keil5/ARM/PACK/Nationstech/*DFP/*/svd/*.svd；
也可 --svd 显式指定（用户芯片无 pack 时）。

读当前值后端：
- --backend jlink   JLink Commander halt+mem32（J-Link 调试器）
- --backend openocd OpenOCD TCL 端口 mdw（ST-Link/DAP，运行中读，不 halt）
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
for _candidate in [_SCRIPT_DIR / "shared", _SCRIPT_DIR.parent / "shared",
                   _SCRIPT_DIR.parents[1] / "shared", _SCRIPT_DIR.parents[2] / "shared"]:
    if (_candidate / "tool_config.py").exists():
        sys.path.insert(0, str(_candidate))
        break
try:
    from tool_config import get_tool_path
except ImportError:
    get_tool_path = None  # type: ignore

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


@dataclass
class SvdField:
    name: str
    bit_offset: int
    bit_width: int
    description: str = ""
    enum: dict[int, str] = field(default_factory=dict)  # value -> name


@dataclass
class SvdRegister:
    name: str
    offset: int
    size: int = 32
    access: str = ""
    description: str = ""
    reset_value: int | None = None
    reset_mask: int | None = None
    fields: list[SvdField] = field(default_factory=list)


@dataclass
class SvdPeripheral:
    name: str
    base: int
    description: str = ""
    registers: list[SvdRegister] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SVD 自动发现
# ---------------------------------------------------------------------------


def _uv4_to_pack_dirs() -> list[Path]:
    """从 tool_config 的 uv4 路径推导 Keil pack 目录。"""
    if not get_tool_path:
        return []
    uv4 = get_tool_path("uv4")
    if not uv4:
        return []
    p = Path(uv4)
    keil_root = p.parents[1]  # .../Keil5/UV4/UV4.exe → Keil5
    return [keil_root / "ARM" / "PACK", Path(os.environ.get("LOCALAPPDATA", "")) / "Arm" / "Packs"]


def find_svd_files() -> list[tuple[str, Path]]:
    """扫描所有 pack 目录的 SVD，返回 [(chip_lower, path)]。"""
    found: dict[str, Path] = {}
    dirs = _uv4_to_pack_dirs()
    for pd in dirs:
        if not pd.exists():
            continue
        for svd in pd.rglob("*.svd"):
            key = svd.stem.lower()
            if key not in found:
                found[key] = svd
    # 按 chip 名排序
    return sorted(found.items())


def resolve_svd(chip: str | None, explicit: str | None) -> tuple[Path | None, str]:
    """解析 SVD 路径：显式 --svd > --chip 匹配 > 自动（唯一或提示）。"""
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p, ""
        return None, f"❌ SVD 文件不存在: {explicit}"
    svds = find_svd_files()
    if not svds:
        return None, "❌ 未找到任何 SVD（请用 --svd 显式指定，或安装芯片 DFP pack）"
    if chip:
        for key, path in svds:
            if key == chip.lower():
                return path, ""
        names = ", ".join(k for k, _ in svds)
        return None, f"❌ 未找到芯片 {chip} 的 SVD（可用: {names}；或 --svd 显式指定）"
    if len(svds) == 1:
        return svds[0][1], ""
    names = ", ".join(f"{k}→{p.parent.name}" for k, p in svds[:12])
    return None, f"❌ 检测到多个 SVD，请用 --chip 指定（可用: {names}；或 --svd 显式指定）"


# ---------------------------------------------------------------------------
# SVD 解析
# ---------------------------------------------------------------------------


def _xtext(node: ET.Element | None, tag: str) -> str:
    if node is None:
        return ""
    el = node.find(tag)
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _xint(node: ET.Element | None, tag: str) -> int | None:
    s = _xtext(node, tag)
    if not s:
        return None
    try:
        return int(s, 0) if s.lower().startswith("0x") else int(s, 10)
    except ValueError:
        return None


def _parse_fields(fields_node: ET.Element | None) -> list[SvdField]:
    fields: list[SvdField] = []
    if fields_node is None:
        return fields
    for fnode in fields_node.findall("field"):
        f = SvdField(
            name=_xtext(fnode, "name"),
            bit_offset=_xint(fnode, "bitOffset") or 0,
            bit_width=_xint(fnode, "bitWidth") or 1,
            description=_xtext(fnode, "description"),
        )
        enum_node = fnode.find("enumeratedValues")
        if enum_node is not None:
            for en in enum_node.findall("enumeratedValue"):
                ename = _xtext(en, "name")
                evalue = _xint(en, "value")
                if evalue is not None and ename:
                    f.enum[evalue] = ename
        fields.append(f)
    fields.sort(key=lambda x: x.bit_offset)
    return fields


def parse_svd(svd_path: str | Path) -> tuple[list[SvdPeripheral] | None, str]:
    """解析 SVD 为外设列表（处理 peripheral/register 的 derivedFrom 继承）。

    返回 (peripherals, "") 或 (None, 错误信息)。
    """
    path = Path(svd_path)
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        return None, f"❌ SVD XML 解析失败: {exc}"
    root = tree.getroot()

    periph_nodes = root.findall(".//peripheral")
    if not periph_nodes:
        return None, "❌ SVD 中未找到任何 peripheral"

    # 先建基础表（含 derivedFrom 标记）
    raw: dict[str, dict] = {}
    order: list[str] = []
    for pnode in periph_nodes:
        name = _xtext(pnode, "name")
        if not name:
            continue
        base = _xint(pnode, "baseAddress")
        if base is None:
            continue
        raw[name] = {
            "node": pnode,
            "base": base,
            "derived": pnode.get("derivedFrom"),
            "description": _xtext(pnode, "description"),
        }
        order.append(name)

    # 解析寄存器（derived 时从父复制，base 用子自己的）
    result: list[SvdPeripheral] = []
    resolved: dict[str, SvdPeripheral] = {}

    def _resolve_periph(name: str) -> SvdPeripheral | None:
        if name in resolved:
            return resolved[name]
        info = raw.get(name)
        if not info:
            return None
        regs: list[SvdRegister] = []
        if info["derived"]:
            parent = _resolve_periph(info["derived"])
            if parent:
                # 派生外设继承父寄存器（地址偏移不变）
                for r in parent.registers:
                    regs.append(SvdRegister(name=r.name, offset=r.offset, size=r.size,
                                            access=r.access, description=r.description,
                                            reset_value=r.reset_value, reset_mask=r.reset_mask,
                                            fields=r.fields))
        regs_node = info["node"].find("registers")
        if regs_node is not None:
            # 同文件 register 也可能 derivedFrom
            reg_cache: dict[str, SvdRegister] = {}

            def _resolve_reg(rnode: ET.Element) -> SvdRegister | None:
                rname = _xtext(rnode, "name")
                if not rname:
                    return None
                if rname in reg_cache:
                    return reg_cache[rname]
                r_derived = rnode.get("derivedFrom")
                if r_derived and r_derived in reg_cache:
                    parent_r = reg_cache[r_derived]
                    reg = SvdRegister(name=rname, offset=parent_r.offset, size=parent_r.size,
                                      access=parent_r.access, description=parent_r.description,
                                      reset_value=parent_r.reset_value,
                                      reset_mask=parent_r.reset_mask,
                                      fields=list(parent_r.fields))
                    reg_cache[rname] = reg
                    return reg
                off = _xint(rnode, "addressOffset")
                if off is None:
                    return None
                reg = SvdRegister(
                    name=rname,
                    offset=off,
                    size=_xint(rnode, "size") or 32,
                    access=_xtext(rnode, "access"),
                    description=_xtext(rnode, "description"),
                    reset_value=_xint(rnode, "resetValue"),
                    reset_mask=_xint(rnode, "resetMask"),
                    fields=_parse_fields(rnode.find("fields")),
                )
                reg_cache[rname] = reg
                return reg

            for rnode in regs_node.findall("register"):
                r = _resolve_reg(rnode)
                if r and not any(x.name == r.name for x in regs):
                    regs.append(r)

        periph = SvdPeripheral(name=name, base=info["base"],
                               description=info["description"], registers=regs)
        resolved[name] = periph
        return periph

    for name in order:
        p = _resolve_periph(name)
        if p:
            result.append(p)
    if not result:
        return None, "❌ SVD 解析结果为空"
    return result, ""


def find_peripheral(periphs: list[SvdPeripheral], name: str) -> SvdPeripheral | None:
    for p in periphs:
        if p.name == name:
            return p
    return None


def find_register(periph: SvdPeripheral, name: str) -> SvdRegister | None:
    for r in periph.registers:
        if r.name == name:
            return r
    return None


def fuzzy_search(periphs: list[SvdPeripheral], keyword: str, limit: int = 30) -> list[str]:
    """子串匹配（不区分大小写）。"timer" 匹配不到 "TIM1" —— 那是子串匹配的固有行为，
    不是大小写问题。无命中时由 suggest_names() 兜底给近邻。"""
    kw = keyword.lower()
    hits: list[str] = []
    for p in periphs:
        if kw in p.name.lower():
            hits.append(f"{p.name}  (外设)")
            continue
        for r in p.registers:
            if kw in r.name.lower():
                hits.append(f"{p.name}.{r.name}")
                if len(hits) >= limit:
                    return hits
    return hits


def suggest_names(periphs: list[SvdPeripheral], keyword: str, limit: int = 5) -> list[str]:
    """无命中时给最接近的名字，返回**原始大小写**的名字。

    没有这个兜底，`--find timer`（SVD 里叫 TIM1）只会得到"未找到"，读起来像"这颗芯片
    没有定时器"—— 于是 AI 转头去翻手册。给几个近邻，真实情况立刻清楚。

    difflib 的相似度是**大小写敏感**的（'timer' 对 'TIM1' 相似度≈0），所以比对在
    小写空间做，返回时再换回原名。
    """
    index: dict[str, str] = {}
    for p in periphs:
        index.setdefault(p.name.lower(), p.name)
        for r in p.registers:
            index.setdefault(f"{p.name}.{r.name}".lower(), f"{p.name}.{r.name}")
    if not index:
        return []
    near = difflib.get_close_matches(keyword.lower(), list(index), n=limit, cutoff=0.3)
    return [index[k] for k in near]


# ---------------------------------------------------------------------------
# 读当前值后端
# ---------------------------------------------------------------------------

# OpenOCD TCL 端口读内存（无停机）。响应例: "0x40021000: 00000001 00000002 ...\x1a"
def _ocd_tcl(port: int, cmd: str, timeout: float = 3.0) -> str | None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            s.sendall((cmd + "\x1a").encode("ascii"))
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\x1a" in chunk:
                    break
            return data.decode("utf-8", errors="replace").replace("\x1a", "").strip()
    except OSError as exc:
        return f"__TCL_ERR__ {exc}"


def ocd_read_words(port: int, addr: int, count: int = 1) -> dict[int, int] | None:
    resp = _ocd_tcl(port, f"mdw 0x{addr:08X} {count}")
    if not resp or resp.startswith("__TCL_ERR__"):
        return None
    values: dict[int, int] = {}
    for line in resp.splitlines():
        m = re.match(r"0x([0-9a-fA-F]+):\s*(.*)", line)
        if not m:
            continue
        base = int(m.group(1), 16)
        for i, w in enumerate(re.findall(r"[0-9A-Fa-f]{8}", m.group(2))):
            values[base + i * 4] = int(w, 16)
    return values


def _run_jlink_read(addr: int, width: int, count: int) -> dict[int, int] | None:
    """JLink Commander 单发 halt+mem+go 读内存。"""
    jlink = get_tool_path("jlink") if get_tool_path else None
    if not jlink or not Path(jlink).exists():
        print("❌ 未找到 JLink.exe（--backend jlink 需要；或改用 --backend openocd）")
        return None
    cmd = "mem32" if width == 4 else "mem8"
    script = "\n".join([
        "si SWD", "speed 4000", "device N32G4FRRE", "connect",
        "halt", f"{cmd} 0x{addr:08X} {count}", "go", "exit",
    ]) + "\n"
    fd, tmp = tempfile.mkstemp(suffix=".jlink", prefix="ea_svd_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        res = subprocess.run([jlink, "-CommanderScript", tmp],
                             capture_output=True, text=True, timeout=30)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    values: dict[int, int] = {}
    for line in res.stdout.splitlines():
        m = re.match(r"^([0-9A-Fa-f]+)\s*=\s*(.*)$", line.strip())
        if not m:
            continue
        try:
            base = int(m.group(1), 16)
        except ValueError:
            continue
        words = re.findall(r"[0-9A-Fa-f]{8}", m.group(2))
        for i, w in enumerate(words):
            try:
                values[base + i * 4] = int(w, 16)
            except ValueError:
                continue
    return values or None


def read_register_value(addr: int, backend: str, ocd_port: int) -> int | None:
    """读 32 位寄存器当前值。"""
    if backend == "openocd":
        vals = ocd_read_words(ocd_port, addr, 1)
        return vals.get(addr) if vals else None
    vals = _run_jlink_read(addr, 4, 1)
    return vals.get(addr) if vals else None


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def reg_to_dict(p: SvdPeripheral, r: SvdRegister) -> dict[str, Any]:
    return {
        "peripheral": p.name,
        "register": r.name,
        "address": f"0x{p.base + r.offset:08X}",
        "offset": f"0x{r.offset:04X}",
        "size_bits": r.size,
        "access": r.access or "",
        "reset_value": f"0x{r.reset_value:08X}" if r.reset_value is not None else None,
        "reset_mask": f"0x{r.reset_mask:08X}" if r.reset_mask is not None else None,
        "description": r.description,
        "fields": [
            {
                "name": f.name,
                "bits": f"[{f.bit_offset + f.bit_width - 1}:{f.bit_offset}]",
                "mask": f"0x{((1 << f.bit_width) - 1) << f.bit_offset:08X}",
                "description": f.description,
                "enum": {f"0x{v:X}": n for v, n in sorted(f.enum.items())} or None,
            }
            for f in r.fields
        ],
    }


def print_reg(p: SvdPeripheral, r: SvdRegister, current: int | None) -> None:
    print(f"📍 {p.name}.{r.name}  @ 0x{p.base + r.offset:08X}  "
          f"(offset 0x{r.offset:04X}, {r.size}-bit, {r.access or 'n/a'})")
    if r.description:
        print(f"   {r.description}")
    if current is not None:
        print(f"   当前值: 0x{current:08X}")
    for f in r.fields:
        bits = f"[{f.bit_offset + f.bit_width - 1}:{f.bit_offset}]"
        mask = ((1 << f.bit_width) - 1) << f.bit_offset
        line = f"   {f.name:<28} {bits:<10} mask=0x{mask:08X}"
        if current is not None:
            fval = (current & mask) >> f.bit_offset
            if f.enum and fval in f.enum:
                line += f"  = {fval} ({f.enum[fval]})"
            else:
                line += f"  = {fval}"
        print(line)
        if f.enum:
            for v, n in sorted(f.enum.items()):
                mark = " ◀" if current is not None and ((current & mask) >> f.bit_offset) == v else ""
                print(f"      {v}: {n}{mark}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="svd_tool.py",
        description="SVD 寄存器解析（地址/位域/枚举/当前值）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --detect
  %(prog)s --periph GPIOA
  %(prog)s --reg RCC CR
  %(prog)s --read GPIOA MODER --backend openocd
  %(prog)s --find timer
  %(prog)s --reg RCC CR --svd N32G4FR.svd
        """,
    )
    act = parser.add_mutually_exclusive_group()
    act.add_argument("--detect", action="store_true", help="列出可用 SVD 文件")
    act.add_argument("--periph", metavar="<外设>", help="列出外设下所有寄存器")
    act.add_argument("--reg", metavar="<外设> <寄存器>", nargs=2, help="寄存器详情")
    act.add_argument("--read", metavar="<外设> <寄存器>", nargs=2, help="读当前值 + 位域解码")
    act.add_argument("--find", metavar="<关键词>", help="模糊搜索外设/寄存器名")

    parser.add_argument("--svd", help="显式指定 SVD 文件路径")
    parser.add_argument("--chip", help="按芯片名匹配 SVD（如 N32G4FR）")
    parser.add_argument("--backend", choices=["jlink", "openocd"], default="openocd",
                        help="读当前值后端（默认 openocd，无停机）")
    parser.add_argument("--ocd-port", type=int, default=6666, help="OpenOCD TCL 端口（默认 6666）")
    parser.add_argument("--json", action="store_true", help="结构化 JSON 输出")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.detect:
        svds = find_svd_files()
        if not svds:
            print("❌ 未找到任何 SVD 文件（请用 --svd 指定，或安装芯片 DFP pack）")
            return 1
        print("📦 检测到 SVD 文件：")
        for key, path in svds:
            size_kb = path.stat().st_size // 1024
            print(f"  {key:<12} {path}  ({size_kb}KB)")
        return 0

    svd_path, err = resolve_svd(args.chip, args.svd)
    if svd_path is None:
        print(err)
        return 1
    periphs, err = parse_svd(svd_path)
    if periphs is None:
        print(err)
        return 1

    if args.find:
        hits = fuzzy_search(periphs, args.find)
        if not hits:
            print(f"❌ 未找到含 '{args.find}' 的外设/寄存器")
            near = suggest_names(periphs, args.find)
            if near:
                print("   最接近的名字：")
                for name in near:
                    print(f"     {name}")
            print("   → 搜索是子串匹配（不区分大小写）：'timer' 匹配不到 'TIM1'，"
                  "改试 'TIM' 或 'TIM1'")
            return 1
        print(f"🔍 搜索 '{args.find}'（{len(hits)} 个）：")
        for h in hits:
            print(f"   {h}")
        return 0

    if args.periph:
        p = find_peripheral(periphs, args.periph)
        if not p:
            print(f"❌ 外设 {args.periph} 未找到")
            return 1
        print(f"📍 {p.name}  @ 0x{p.base:08X}  ({len(p.registers)} 个寄存器)")
        if p.description:
            print(f"   {p.description}")
        print("   寄存器:")
        for r in p.registers:
            print(f"     {r.name:<20} 0x{p.base + r.offset:08X}  (0x{r.offset:04X})")
        return 0

    if args.reg or args.read:
        is_read = bool(args.read)
        periph_name, reg_name = (args.read or args.reg)
        p = find_peripheral(periphs, periph_name)
        if not p:
            print(f"❌ 外设 {periph_name} 未找到")
            return 1
        r = find_register(p, reg_name)
        if not r:
            print(f"❌ 寄存器 {periph_name}.{reg_name} 未找到")
            return 1

        current = None
        if is_read:
            current = read_register_value(p.base + r.offset, args.backend, args.ocd_port)
            if current is None:
                print(f"⚠️  无法读取当前值（{args.backend} 后端）——寄存器定义如下：")
                print("   OpenOCD 后端需先启动 OpenOCD（TCL 端口 {}）；J-Link 后端需 J-Link 已连接".format(args.ocd_port))

        if args.json:
            d = reg_to_dict(p, r)
            if current is not None:
                d["current_value"] = f"0x{current:08X}"
            print(json.dumps(d, ensure_ascii=False, indent=2))
            return 0

        print_reg(p, r, current)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
