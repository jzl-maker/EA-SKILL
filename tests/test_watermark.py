# -*- coding: utf-8 -*-
"""doc_reader 水印过滤回归测试。

重点验证两件相反的事：
  1. 水印 PDF 能被清理（不静默、不残留）
  2. **正常文档不被误伤** —— 误删正文比漏过滤水印严重得多
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                          / "tools" / "doc-reader" / "scripts"))
import doc_reader as dr   # noqa: E402

results = []


def check(label, cond, detail=""):
    results.append(cond)
    print(("[PASS] " if cond else "[FAIL] ") + label)
    if not cond:
        print("        " + detail)


# ---------- 1. 水印检测：本规格书的水印字集应被识别 ----------
wm = "仅供东屋参考"
text = "\n".join([wm[i % 6] for i in range(200)] + ["1. 简介", "正文内容"])
chars, hit = dr.detect_watermark_chars(text.splitlines())
check("水印字集被识别", chars == set(wm), f"得到 {chars}")
check("水印行计入 hit", len(hit) == 6 and all(v >= 20 for v in hit.values()), str(hit))

# ---------- 2. 正常文档：不应有任何水印判定 ----------
normal = [
    "1. 简介", "OM100-YWT100是一款新型面阵式半导体指纹模组，",
    "- 支持 3.3V 供电", "● 传感器表面覆盖保护涂层", "参考电压为 3.3V",
    "供应电流 <25mA", "| 参数 | 描述 | 值 |", "屋", "东",       # 偶发单字，但频次远不到阈值
]
chars_n, hit_n = dr.detect_watermark_chars(normal)
check("正常文档不判水印", chars_n == set() and hit_n == {}, f"误判为 {chars_n} / {hit_n}")

# ---------- 3. 项目符号安全阀：同一种短行高频 ≠ 水印 ----------
bullets = ["■"] * 60 + ["正文", "更多正文"]
chars_b, hit_b = dr.detect_watermark_chars(bullets)
check("单一项目符号不判水印", chars_b == set(), f"误判为 {chars_b}")

# ---------- 4. clean_line：两侧有分隔才剥离 ----------
check("行首分隔水印字被剥离", dr.clean_line("参 57600", set(wm)) == "57600")
check("行尾分隔水印字被剥离",
      dr.clean_line("(www.microctopus.com) 考", set(wm)) == "(www.microctopus.com)")
check("正常行首符号不动", dr.clean_line("- item", set(wm)) == "- item")
check("正常行首星号不动", dr.clean_line("* note", set(wm)) == "* note")

# ---------- 5. 关键安全用例：不能改坏以水印字开头的正常词 ----------
check("【安全】'参考电压' 不被截断",
      dr.clean_line("参考电压为3.3V", set(wm)) == "参考电压为3.3V",
      dr.clean_line("参考电压为3.3V", set(wm)))
check("【安全】'供应电流' 不被截断",
      dr.clean_line("供应电流<25mA", set(wm)) == "供应电流<25mA")
check("【安全】紧贴的无分隔水印字保守保留",
      dr.clean_line("考版本V1.0.5", set(wm)) == "考版本V1.0.5")

# ---------- 6. strip_watermark：行级过滤 ----------
lines = ["屋", "东", "正文一", "考 57600", "参考电压", ""]
kept, dropped = dr.strip_watermark(lines, set(wm))
check("纯水印行被删且计数正确", dropped == 2, f"dropped={dropped}")
check("正文行保留、水印字剥离、空行保留",
      kept == ["正文一", "57600", "参考电压", ""], str(kept))

# ---------- 7. 端到端：整篇 Doc 清理 ----------
doc = dr.Doc(path=Path("x.pdf"), fmt="pdf")
doc.sections.append(dr.Section(kind="page", level=1, text=text))
doc.sections.append(dr.Section(kind="table", level=1,
                               rows=[["屋 接口", "6", "参"], ["东", "pin", "供"]]))
dr.remove_watermark(doc)
check("表格单元格水印被清理",
      doc.sections[1].rows == [["接口", "6", ""], ["", "pin", ""]],
      str(doc.sections[1].rows))
check("meta 打上 watermark 标记", doc.meta.get("watermark") == 1)
check("warning 明确告知（不静默）",
      any("平铺水印" in w and "keep-watermark" in w for w in doc.warnings),
      str(doc.warnings))

# ---------- 8. 无水的 Doc 应完全不动 ----------
doc2 = dr.Doc(path=Path("y.pdf"), fmt="pdf")
body = "1. 简介\n本模组采用电参容式指纹传感器。\n- 支持 3.3V 供电"
doc2.sections.append(dr.Section(kind="page", level=1, text=body))
before = doc2.sections[0].text
dr.remove_watermark(doc2)
check("正常文档内容零改动", doc2.sections[0].text == before, repr(doc2.sections[0].text))
check("正常文档不加 warning", doc2.warnings == [] and "watermark" not in doc2.meta)

# ---------- 9. 比例阈值：大文档里重复出现的短标题不算水印 ----------
big = ["注意"] * 200 + ["警告"] * 150 + ["说明"] * 120 + ["正常正文内容行"] * 9500
chars_big, _ = dr.detect_watermark_chars(big)
check("大文档重复短标题不判水印", chars_big == set(), f"误判为 {chars_big}")

# ---------- 10. 比例阈值：单页切片仍能检出（固定阈值 20 会漏判）----------
one_page = [_ for _ in "仅供东屋参考"] * 9 + [
    "第%d行正常正文内容" % i for i in range(50)]
chars_1p, _ = dr.detect_watermark_chars(one_page)
check("单页切片仍能检出", chars_1p == set("仅供东屋参考"), f"得到 {chars_1p}")

# ---------- 11. 比例阈值：短文档里的偶然重复不误判 ----------
tiny = ["屋", "东", "参", "考", "正文一", "正文二"]
chars_t, _ = dr.detect_watermark_chars(tiny)
check("短文档偶然重复不误判", chars_t == set(), f"误判为 {chars_t}")

# ---------- 12. 多字高频短行不应污染水印字符集 ----------
mixed = [_ for _ in "仅供东屋参考"] * 10 + ["___"] * 40 + ["正文内容"]
chars_m, _ = dr.detect_watermark_chars(mixed)
check("多字短行不混入水印字符集",
      chars_m == set("仅供东屋参考"), f"得到 {chars_m}")

print("─" * 50)
print(f"{sum(results)}/{len(results)} 通过")
sys.exit(0 if all(results) else 1)
