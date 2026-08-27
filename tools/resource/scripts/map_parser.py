#!/usr/bin/env python
"""Keil .map 文件解析器（armcc v5 / armclang v6），供 /ea size 与 /ea map 共享。

纯标准库，无第三方依赖。解析输出五类信息：

  totals   Total RO / RW / ROM Size（Flash/RAM 总量直接来源）
  regions  Execution Region 列表（size/max，容量来自匹配 IROM/FLASH/ROM 与 IRAM/RAM 的 Max）
  objects  Image component sizes 逐对象 Code/RO/RW/ZI/Debug
  symbols  Global Symbols 符号表（name/value/type/size/obj）
  stack/heap  STACK/HEAP Section 符号的分配量

真实格式参考（armclang v6）:
  Total RO  Size (Code + RO Data)               114200 ( 111.52kB)
  Total RW  Size (RW Data + ZI Data)             74264 (  72.52kB)
  Total ROM Size (Code + RO Data + RW Data)     114384 ( 111.70kB)

  Execution Region ER_IROM1 (Exec base: 0x08000000, Load base: 0x08000000, Size: 0x0001be18, Max: 0x00080000, ABSOLUTE)
  Execution Region RW_IRAM1 (Exec base: 0x20000000, Load base: 0x0801be18, Size: 0x00012218, Max: 0x00024000, ABSOLUTE, COMPRESSED[...])

  Image component sizes
      Code (inc. data)   RO Data    RW Data    ZI Data      Debug   Object Name
      1226         58          0          0        720      12328   blesdk.o
      ...
      3672        136        135         28          0       2820   Library Totals

  Global Symbols
    Symbol Name                              Value     Ov Type        Size  Object(Section)
    HEAP                                     0x20002218   Section    32768  startup_n32g4fr.o(HEAP)
    STACK                                    0x2000a218   Section    32768  startup_n32g4fr.o(STACK)
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# section 分隔线：'======='（顶层 section 边界），用于结束当前解析状态
_SECTION_BAR = "="

_TOTAL_RE = re.compile(r"Total\s+(RO|RW|ROM)\s+Size.*?\b(\d+)\b")
_REGION_RE = re.compile(
    r"Execution Region (\S+) \(Exec base: (0x[0-9a-fA-F]+), "
    r"Load base: (0x[0-9a-fA-F]+), Size: (0x[0-9a-fA-F]+), "
    r"Max: (0x[0-9a-fA-F]+)"
)
_OBJECT_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.+)$"
)

_FLASH_REGION = re.compile(r"(?i)irom|flash|rom")
_RAM_REGION = re.compile(r"(?i)iram|ram|zi")


@dataclass
class Region:
    name: str
    exec_base: int
    load_base: int
    size: int
    max: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ObjectSize:
    name: str
    code: int
    ro: int
    rw: int
    zi: int
    debug: int

    @property
    def total(self) -> int:
        return self.code + self.ro + self.rw + self.zi

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total"] = self.total
        return d


@dataclass
class Symbol:
    name: str
    value: int
    type: str
    size: int
    obj: str

    def to_dict(self) -> dict:
        return asdict(self)


def _read_text(path: Path) -> str:
    """读取 .map，兼容 UTF-8 与 GBK（工程路径含中文时）。"""
    raw = path.read_bytes()
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def parse_map(path: str | Path) -> dict:
    """解析 Keil .map，返回结构化 dict。

    raises: FileNotFoundError（文件不存在）、ValueError（非 Keil .map / 无法定位总量）
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"找不到 .map 文件: {p}")

    text = _read_text(p)
    if "Image component sizes" not in text and "Execution Region" not in text:
        raise ValueError(f"非 Keil .map 格式（缺 Execution Region / Image component sizes）: {p.name}")

    totals: dict[str, int] = {}
    regions: list[Region] = []
    objects: list[ObjectSize] = []
    symbols: list[Symbol] = []

    in_objects = False
    in_symbols = False

    for line in text.splitlines():
        if not in_objects and not in_symbols:
            # Total / Region 全局匹配
            m = _TOTAL_RE.search(line)
            if m:
                totals[m.group(1)] = int(m.group(2))
                continue
            m = _REGION_RE.search(line)
            if m:
                regions.append(Region(
                    name=m.group(1),
                    exec_base=int(m.group(2), 16),
                    load_base=int(m.group(3), 16),
                    size=int(m.group(4), 16),
                    max=int(m.group(5), 16),
                ))
                continue

        if line.startswith(_SECTION_BAR):
            in_objects = in_symbols = False
            continue
        if "Image component sizes" in line:
            in_objects = True
            in_symbols = False
            continue
        if "Image Symbol Table" in line or line.strip() == "Global Symbols":
            # 本地符号表（含 STACK/HEAP/.bss 段）与全局符号表，列格式一致
            in_objects = False
            in_symbols = True
            continue

        if in_objects:
            _collect_object(line, objects)
        elif in_symbols:
            if "Object(Section)" in line and "Value" in line:
                continue  # 表头
            _collect_symbol(line, symbols)

    stack_bytes = heap_bytes = None
    for s in symbols:
        if s.type == "Section":
            if s.name == "STACK":
                stack_bytes = s.size
            elif s.name == "HEAP":
                heap_bytes = s.size

    flash_capacity = max((r.max for r in regions if _FLASH_REGION.search(r.name)),
                         default=None)
    ram_capacity = max((r.max for r in regions if _RAM_REGION.search(r.name)),
                       default=None)

    return {
        "source": str(p),
        "totals": totals,
        "regions": [r.to_dict() for r in regions],
        "objects": [o.to_dict() for o in objects],
        "symbols": [s.to_dict() for s in symbols],
        "flash_capacity": flash_capacity,
        "ram_capacity": ram_capacity,
        "stack_bytes": stack_bytes,
        "heap_bytes": heap_bytes,
    }


def _collect_object(line: str, objects: list[ObjectSize]) -> None:
    """解析 Image component sizes 的一行：6 数字 + 对象名。跳过表头/分隔线/合计。"""
    if "Code (inc. data)" in line or "Object Name" in line:
        return
    if line.lstrip().startswith(("-", "=")):
        return
    m = _OBJECT_RE.match(line)
    if not m:
        return
    name = m.group(7).strip()
    if name.endswith("Totals") or not name:
        return
    objects.append(ObjectSize(
        name=name,
        code=int(m.group(1)),
        ro=int(m.group(2)),
        rw=int(m.group(3)),
        zi=int(m.group(4)),
        debug=int(m.group(5)),
    ))


def _collect_symbol(line: str, symbols: list[Symbol]) -> None:
    """解析符号表一行（本地+全局，列格式一致）。

    列位在 Keil map 的表头与数据行间有 ±1 偏差，不能按列切片；改按 token 结构：
    [name, 0x..., (Ov), type..., size, obj...]。size 是最后一个纯十进制 token，
    type 是它前面的词（可能多词如 "Thumb Code"），obj 是它后面的词（可能含空格）。
    """
    toks = line.split()
    if len(toks) < 3:
        return
    name, vtok = toks[0], toks[1]
    if not vtok.startswith("0x"):
        return
    try:
        value = int(vtok, 16)
    except ValueError:
        return

    rest = toks[2:]
    size_idx = None
    for j in range(len(rest) - 1, -1, -1):
        if rest[j].isdigit():
            size_idx = j
            break
    if size_idx is None:
        return
    size = int(rest[size_idx])
    type_ = " ".join(rest[:size_idx])
    obj = " ".join(rest[size_idx + 1:])
    symbols.append(Symbol(name=name, value=value, type=type_, size=size, obj=obj))


def format_bytes(n: int | None) -> str:
    """字节 → 人类可读（KB 一位小数）。"""
    if n is None:
        return "?"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def ensure_utf8() -> None:
    """兼容 Windows GBK 控制台：强制 UTF-8 输出。"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def emit_json(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def find_map_file(project_dir: str | None = None) -> Path | None:
    """扫描目录（默认当前目录）找最新 .map。"""
    base = Path(project_dir).resolve() if project_dir else Path.cwd()
    maps = [p for p in base.rglob("*.map") if p.is_file()]
    if not maps:
        return None
    return max(maps, key=lambda p: p.stat().st_mtime)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Keil .map 解析器自测")
    parser.add_argument("--map", required=True)
    args = parser.parse_args()
    data = parse_map(args.map)
    print(f"source      : {data['source']}")
    print(f"totals      : {data['totals']}")
    print(f"flash_cap   : {format_bytes(data['flash_capacity'])}")
    print(f"ram_cap     : {format_bytes(data['ram_capacity'])}")
    print(f"stack/heap  : {format_bytes(data['stack_bytes'])} / {format_bytes(data['heap_bytes'])}")
    print(f"regions     : {len(data['regions'])} 个")
    print(f"objects     : {len(data['objects'])} 个")
    print(f"symbols     : {len(data['symbols'])} 个")
