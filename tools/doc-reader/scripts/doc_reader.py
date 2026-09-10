#!/usr/bin/env python
"""嵌入式文档识别：PDF / Word(.docx) / Excel(.xlsx) → Markdown 或 JSON。

设计原则:
  - DOCX / XLSX 本质是 zip + XML → 用标准库 zipfile + xml.etree **零依赖**解析
  - PDF 需要文本层（pdfplumber）；扫描件（无文本层）明确报错，不做 OCR
  - 解析结果按内容哈希缓存在 <STATE_DIR>/docs/ 下，二次读取命中缓存

用法:
  py doc_reader.py <文件>                 # Markdown（表格超 20 行截断）
  py doc_reader.py <文件> --outline       # 只输出结构大纲
  py doc_reader.py <文件> --full          # 不截断
  py doc_reader.py <文件> --tables        # 只输出表格
  py doc_reader.py <文件> --pages 1-20    # PDF 页范围（也支持 1,5,7-9）
  py doc_reader.py <文件> --sheet Sheet1  # 只解析指定工作表（可多次）
  py doc_reader.py <文件> --json          # 结构化 JSON（供 AI 消费）
  py doc_reader.py --scan <目录>          # 列出目录下可识别文档

返回码: 0=成功 / 1=解析失败（含不支持的格式）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

# 兼容 Windows GBK 控制台
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SUPPORTED = {".pdf": "PDF", ".docx": "Word", ".xlsx": "Excel"}
# 常见但不在支持范围的格式 → 给出转换建议
UNSUPPORTED_HINT = {
    ".doc": "老版 .doc 二进制格式不支持 → 用 Word 另存为 .docx",
    ".xls": "老版 .xls 二进制格式不支持 → 用 Excel 另存为 .xlsx",
    ".pptx": "暂不支持 PPTX → 可另存为 PDF 后用 /ea doc 解析",
    ".ppt": "暂不支持 PPT → 可另存为 PDF 后用 /ea doc 解析",
    ".csv": "CSV 直接用 Read 工具读取即可",
    ".txt": "纯文本直接用 Read 工具读取即可",
    ".md": "Markdown 直接用 Read 工具读取即可",
}

TABLE_PREVIEW_ROWS = 20     # 默认表格展示行数（--full 解除）
OUTLINE_SNIPPET = 200       # --outline 每节摘录字符数
DEFAULT_MAX_ROWS = 1000     # 每个工作表默认解析上限（--full / --max-rows 覆盖）

# OOXML 命名空间
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


class DocError(Exception):
    """可预期的解析错误 → 友好提示，不打印 traceback。"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class Section:
    kind: str = "paragraph"                     # heading | paragraph | table | page | sheet
    text: str = ""
    level: int = 0                              # heading 级别 / page 页码 / 表格序号
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Doc:
    path: Path
    fmt: str
    sections: list[Section] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "format": self.fmt,
            "meta": self.meta,
            "warnings": self.warnings,
            "sections": [
                {"kind": s.kind, "text": s.text, "level": s.level, "rows": s.rows}
                for s in self.sections
            ],
        }

    @staticmethod
    def from_dict(d: dict) -> "Doc":
        doc = Doc(path=Path(d["path"]), fmt=d["format"],
                  meta=d.get("meta", {}), warnings=d.get("warnings", []))
        doc.sections = [
            Section(kind=s["kind"], text=s.get("text", ""),
                    level=s.get("level", 0), rows=s.get("rows", []))
            for s in d.get("sections", [])
        ]
        return doc


# ---------------------------------------------------------------------------
# 缓存（<STATE_DIR>/docs/）
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


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def cache_path(src: Path) -> Path | None:
    """缓存路径按内容哈希命名 → 内容变则文件名变，无需失效判断。"""
    sd = state_dir(src.parent)
    if sd is None:
        return None
    stem = re.sub(r"[^0-9A-Za-z_.-]+", "_", src.stem)[:40] or "doc"
    digest = file_sha256(src)[:8]
    out = sd / "docs"
    out.mkdir(parents=True, exist_ok=True)
    return out / f"{stem}-{digest}.json"


# ---------------------------------------------------------------------------
# PDF（pdfplumber，需文本层）
# ---------------------------------------------------------------------------


def parse_pages(spec: str | None, total: int) -> list[int]:
    """解析 --pages："1-20" / "3" / "1,5,7-9" → 0-based 索引列表（去重升序）。"""
    if not spec:
        return list(range(total))
    picked: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            picked.update(range(max(lo, 1) - 1, min(hi, total)))
            continue
        if part.isdigit():
            n = int(part)
            if 1 <= n <= total:
                picked.add(n - 1)
    return sorted(picked)


def read_pdf(path: Path, pages: str | None) -> Doc:
    try:
        import pdfplumber
    except ImportError:
        raise DocError(
            "未安装 pdfplumber（PDF 解析依赖）。\n"
            "  安装: py -m pip install -r "
            f"{Path(__file__).resolve().parent.parent / 'requirements.txt'}\n"
            "  或: py <skill>/tools/doc-reader/scripts/deps_check.py --install"
        )

    doc = Doc(path=path, fmt="pdf")
    with pdfplumber.open(str(path)) as pdf:
        total = len(pdf.pages)
        idx = parse_pages(pages, total)
        if not idx:
            raise DocError(f"--pages {pages} 超出范围（该 PDF 共 {total} 页）")
        with_text = 0
        n_table = 0
        for i in idx:
            page = pdf.pages[i]
            try:
                text = (page.extract_text() or "").strip()
            except Exception as exc:  # noqa: BLE001 - 单页解析失败不应终止整篇
                doc.warnings.append(f"第 {i + 1} 页文本提取失败: {exc}")
                text = ""
            if text:
                with_text += 1
            doc.sections.append(Section(kind="page", text=text, level=i + 1))
            try:
                tables = page.extract_tables() or []
            except Exception as exc:  # noqa: BLE001
                doc.warnings.append(f"第 {i + 1} 页表格提取失败: {exc}")
                tables = []
            for t in tables:
                rows = [[(c or "").strip() for c in row] for row in t]
                if any(any(c for c in r) for r in rows):
                    n_table += 1
                    doc.sections.append(Section(kind="table", rows=rows, level=n_table))
        doc.meta.update({
            "pages_total": total,
            "pages_parsed": len(idx),
            "pages_with_text": with_text,
            "tables": n_table,
        })

    if with_text == 0:
        raise DocError(
            f"PDF 无文本层（解析的 {len(idx)} 页均提取不到文字）——大概率是扫描件/图片型 PDF。\n"
            "扫描件不在 /ea doc 支持范围（不做 OCR）。\n"
            "替代方案: 直接用 Read 工具读该 PDF（走视觉识别）。"
        )
    if with_text < len(idx):
        doc.warnings.append(
            f"{len(idx) - with_text} 页无文本层（可能是扫描页/纯图页），已跳过其文本"
        )
    return doc


# ---------------------------------------------------------------------------
# DOCX（zipfile + XML，零依赖）
# ---------------------------------------------------------------------------


def _docx_para_text(p: ET.Element) -> str:
    """按文档顺序拼接段落内文本（含 <w:tab>/<w:br>）。"""
    parts: list[str] = []
    for node in p.iter():
        if node.tag == f"{W}t":
            parts.append(node.text or "")
        elif node.tag == f"{W}tab":
            parts.append("\t")
        elif node.tag == f"{W}br":
            parts.append("\n")
    return "".join(parts).strip()


def _docx_heading_level(p: ET.Element) -> int:
    ppr = p.find(f"{W}pPr")
    if ppr is None:
        return 0
    style = ppr.find(f"{W}pStyle")
    if style is None:
        return 0
    val = (style.get(f"{W}val") or "").strip()
    m = re.fullmatch(r"[Hh]eading(\d+)", val)
    if m:
        return int(m.group(1))
    return 1 if val.lower() == "title" else 0


def _docx_table(tbl: ET.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in tbl.findall(f"{W}tr"):
        cells = []
        for tc in tr.findall(f"{W}tc"):
            txt = " ".join(_docx_para_text(p) for p in tc.findall(f"{W}p"))
            cells.append(re.sub(r"\s+", " ", txt).strip())
        rows.append(cells)
    return rows


def read_docx(path: Path) -> Doc:
    doc = Doc(path=path, fmt="docx")
    try:
        with zipfile.ZipFile(path) as z:
            if "word/document.xml" not in z.namelist():
                raise DocError("不是有效的 .docx（缺少 word/document.xml）")
            root = ET.fromstring(z.read("word/document.xml"))
    except zipfile.BadZipFile:
        raise DocError("文件损坏或不是有效的 .docx（zip 解析失败）")

    body = root.find(f"{W}body")
    if body is None:
        raise DocError("docx 正文为空（缺少 <w:body>）")

    n_head = n_table = 0
    for el in body:
        if el.tag == f"{W}p":
            text = _docx_para_text(el)
            if not text:
                continue
            level = _docx_heading_level(el)
            if level:
                n_head += 1
                doc.sections.append(Section(kind="heading", text=text, level=level))
            else:
                doc.sections.append(Section(kind="paragraph", text=text))
        elif el.tag == f"{W}tbl":
            rows = _docx_table(el)
            if rows:
                n_table += 1
                doc.sections.append(Section(kind="table", rows=rows, level=n_table))

    if not doc.sections:
        raise DocError("docx 解析后无内容（可能是纯图片/文本框排版）")
    doc.meta.update({"headings": n_head, "tables": n_table})
    return doc


# ---------------------------------------------------------------------------
# XLSX（zipfile + XML，零依赖）
# ---------------------------------------------------------------------------


def _xlsx_shared_strings(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(f"{X}t")) for si in root.iter(f"{X}si")]


def _xlsx_sheet_map(z: zipfile.ZipFile) -> list[tuple[str, str | None]]:
    """返回 [(sheet 名, zip 内路径)]，保持工作簿顺序。"""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rid_target: dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in z.namelist():
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        for rel in rels:
            rid = rel.get("Id")
            target = (rel.get("Target") or "").replace("\\", "/")
            if not rid or not target:
                continue
            if target.startswith("/"):
                rid_target[rid] = target.lstrip("/")
            elif target.startswith("xl/"):
                rid_target[rid] = target
            else:
                rid_target[rid] = "xl/" + target
    out: list[tuple[str, str | None]] = []
    for i, sh in enumerate(wb.iter(f"{X}sheet")):
        name = sh.get("name") or f"Sheet{i + 1}"
        out.append((name, rid_target.get(sh.get(R_ID) or "")))
    return out


def _col_index(ref: str) -> int:
    """Excel 单元格引用 "B3" → 列索引 1（0-based）。"""
    col = 0
    for ch in ref:
        if ch.isalpha():
            col = col * 26 + (ord(ch.upper()) - ord("A") + 1)
        else:
            break
    return max(col - 1, 0)


def _xlsx_cell_value(c: ET.Element, shared: list[str]) -> str:
    t = c.get("t")
    if t == "s":                                  # sharedStrings 索引
        v = c.find(f"{X}v")
        idx = int(v.text) if v is not None and (v.text or "").strip().isdigit() else -1
        return shared[idx] if 0 <= idx < len(shared) else ""
    if t == "inlineStr":                          # 内联字符串
        is_el = c.find(f"{X}is")
        return "".join(x.text or "" for x in is_el.iter(f"{X}t")) if is_el is not None else ""
    if t == "b":                                  # 布尔
        v = c.find(f"{X}v")
        return "TRUE" if (v is not None and (v.text or "") == "1") else "FALSE"
    v = c.find(f"{X}v")                           # 数字 / 公式缓存值 / 日期序列号
    return (v.text or "") if v is not None else ""


def _xlsx_sheet_rows(z: zipfile.ZipFile, target: str, shared: list[str],
                     max_rows: int) -> tuple[list[list[str]], bool]:
    """返回 (行数据, 是否被截断)。"""
    root = ET.fromstring(z.read(target))
    rows: list[list[str]] = []
    for row in root.iter(f"{X}row"):
        if max_rows and len(rows) >= max_rows:
            return rows, True
        cells: dict[int, str] = {}
        for c in row.findall(f"{X}c"):
            val = _xlsx_cell_value(c, shared).strip()
            if val:
                cells[_col_index(c.get("r") or "A1")] = val
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])
    return rows, False


def read_xlsx(path: Path, sheets: list[str] | None, max_rows: int) -> Doc:
    doc = Doc(path=path, fmt="xlsx")
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise DocError("文件损坏或不是有效的 .xlsx（zip 解析失败）")

    with z:
        shared = _xlsx_shared_strings(z)
        sheet_map = _xlsx_sheet_map(z)
        if sheets:
            wanted = [s.lower() for s in sheets]
            sheet_map = [(n, t) for n, t in sheet_map if n.lower() in wanted]
            if not sheet_map:
                raise DocError(f"找不到工作表 {sheets}；该文件的工作表: "
                               + ", ".join(n for n, _ in _xlsx_sheet_map(z)))

        truncated: list[str] = []
        for name, target in sheet_map:
            if not target or target not in z.namelist():
                doc.warnings.append(f"工作表「{name}」内容缺失，已跳过")
                continue
            rows, cut = _xlsx_sheet_rows(z, target, shared, max_rows)
            if cut:
                truncated.append(name)
            doc.sections.append(Section(kind="sheet", text=name, rows=rows,
                                        level=len(doc.sections) + 1))

    if not doc.sections:
        raise DocError("xlsx 解析后无内容（工作表为空？）")
    doc.meta["sheets"] = [s.text for s in doc.sections]
    if truncated:
        doc.meta["rows_limit"] = max_rows
        doc.warnings.append(
            f"工作表 {truncated} 仅解析前 {max_rows} 行（--full 解除限制）"
        )
    return doc


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def render_markdown(doc: Doc, full: bool, outline: bool, tables_only: bool) -> str:
    lines = [
        f"# {doc.path.name}",
        "",
        f"> 来源: `{doc.path}`",
        f"> 格式: {doc.fmt} | 解析: {datetime.now():%Y-%m-%d %H:%M}",
    ]
    if doc.meta:
        lines.append("> " + " | ".join(f"{k}={v}" for k, v in doc.meta.items()))
    for w in doc.warnings:
        lines.append(f"> ⚠️ {w}")
    lines.append("")

    for s in doc.sections:
        if s.kind == "heading":
            if not tables_only:
                lines += [f"{'#' * min(s.level + 1, 6)} {s.text}", ""]
        elif s.kind == "paragraph":
            if not tables_only:
                lines += [s.text, ""]
        elif s.kind == "page":
            if tables_only:
                continue
            lines += [f"## 第 {s.level} 页", ""]
            if outline:
                snippet = s.text[:OUTLINE_SNIPPET]
                lines += [snippet + ("…" if len(s.text) > OUTLINE_SNIPPET else ""), ""]
            else:
                lines += [s.text, ""]
        elif s.kind == "sheet":
            if not tables_only:
                lines += [f"## 工作表: {s.text}", ""]
                if outline:
                    lines += [f"（{len(s.rows)} 行 × "
                              f"{max((len(r) for r in s.rows), default=0)} 列）", ""]
                    continue
            lines += _render_table(s.rows, full)
        elif s.kind == "table":
            if outline:
                lines += [f"（表格 {s.level}: {len(s.rows)} 行 × "
                          f"{max((len(r) for r in s.rows), default=0)} 列）", ""]
            else:
                lines += _render_table(s.rows, full)
    return "\n".join(lines).rstrip() + "\n"


def _render_table(rows: list[list[str]], full: bool) -> list[str]:
    if not rows:
        return []
    width = max(len(r) for r in rows)
    padded = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(_esc(c) for c in padded[0]) + " |",
           "|" + "---|" * width]
    body = padded[1:]
    if not full and len(body) > TABLE_PREVIEW_ROWS:
        body = body[:TABLE_PREVIEW_ROWS]
        out += ["| " + " | ".join(_esc(c) for c in r) + " |" for r in body]
        out.append(f"| … 省略 {len(padded) - 1 - TABLE_PREVIEW_ROWS} 行（--full 展开）|")
    else:
        out += ["| " + " | ".join(_esc(c) for c in r) + " |" for r in body]
    out.append("")
    return out


def _esc(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


# ---------------------------------------------------------------------------
# 解析入口
# ---------------------------------------------------------------------------


def read_doc(path: Path, pages: str | None = None, sheets: list[str] | None = None,
             max_rows: int = 0) -> Doc:
    if not path.exists():
        raise DocError(f"文件不存在: {path}")
    if path.is_dir():
        raise DocError(f"这是一个目录: {path}（要批量查看请用 --scan {path}）")

    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        hint = UNSUPPORTED_HINT.get(ext, f"不支持的扩展名 {ext or '(无)'}")
        raise DocError(
            f"{hint}\n支持格式: " + " / ".join(f"{v}({k})" for k, v in SUPPORTED.items())
        )

    if ext == ".pdf":
        return read_pdf(path, pages)
    if ext == ".docx":
        if pages:
            raise DocError("--pages 只适用于 PDF")
        return read_docx(path)
    if sheets and ext != ".xlsx":
        raise DocError("--sheet 只适用于 XLSX")
    return read_xlsx(path, sheets, max_rows)


def scan(dir_path: Path) -> int:
    if not dir_path.is_dir():
        print(f"❌ 不是目录: {dir_path}", file=sys.stderr)
        return 1
    ok: list[Path] = []
    other: list[Path] = []
    for p in sorted(dir_path.rglob("*")):
        if not p.is_file() or p.name.startswith("~$"):
            continue
        ext = p.suffix.lower()
        if ext in SUPPORTED:
            ok.append(p)
        elif ext in UNSUPPORTED_HINT:
            other.append(p)

    print(f"📂 {dir_path} — 可识别 {len(ok)} 个\n")
    for p in ok:
        size = p.stat().st_size
        print(f"  ✅ [{SUPPORTED[p.suffix.lower()]:5}] {p.relative_to(dir_path)}  ({size:,} B)")
    if other:
        print(f"\n⬜ 不可识别 {len(other)} 个:")
        for p in other:
            print(f"  ⬜ [{p.suffix.lower():5}] {p.relative_to(dir_path)}"
                  f"  → {UNSUPPORTED_HINT[p.suffix.lower()]}")
    if not ok:
        print("  （无可识别的 PDF / DOCX / XLSX）")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", help="文档路径（.pdf / .docx / .xlsx）")
    parser.add_argument("--scan", metavar="DIR", help="列出目录下可识别文档后退出")
    parser.add_argument("--outline", action="store_true", help="只输出结构大纲")
    parser.add_argument("--full", action="store_true", help="不截断（表格全量）")
    parser.add_argument("--tables", action="store_true", help="只输出表格")
    parser.add_argument("--pages", help="PDF 页范围，如 1-20 或 1,5,7-9")
    parser.add_argument("--sheet", action="append", help="只解析指定工作表（可重复）")
    parser.add_argument("--json", action="store_true", help="结构化 JSON 输出")
    parser.add_argument("--out", metavar="FILE", help="同时写入文件")
    parser.add_argument("--no-cache", action="store_true", help="忽略缓存，强制重新解析")
    parser.add_argument("--max-rows", type=int, default=0,
                        help="每个工作表最多解析行数（0=不限）")
    args = parser.parse_args(argv)

    if args.scan:
        return scan(Path(args.scan))
    if not args.path:
        parser.error("需要提供文档路径，或用 --scan <目录>")

    src = Path(args.path)
    # 默认每表最多 1000 行，防超大表撑爆上下文；--full 或 --max-rows 显式覆盖
    max_rows = 0 if args.full else (args.max_rows or DEFAULT_MAX_ROWS)

    try:
        cp = None if args.no_cache else cache_path(src)
        if cp is not None and cp.is_file():
            doc = Doc.from_dict(json.loads(cp.read_text(encoding="utf-8")))
            doc.warnings.append(f"（缓存命中: {cp.name}）")
        else:
            doc = read_doc(src, pages=args.pages, sheets=args.sheet, max_rows=max_rows)
            if cp is not None:
                cp.write_text(json.dumps(doc.to_dict(), ensure_ascii=False),
                              encoding="utf-8")
    except DocError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - 兜底，避免抛栈给用户
        print(f"❌ 解析失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        text = json.dumps(doc.to_dict(), ensure_ascii=False, indent=2)
    else:
        text = render_markdown(doc, full=args.full, outline=args.outline,
                               tables_only=args.tables)

    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\n💾 已写入 {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
