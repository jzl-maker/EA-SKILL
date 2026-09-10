# EA-SKILL doc-reader 文档识别工具

把嵌入式开发常见的三类文档转成 Markdown / JSON，供 AI 直接消费。

| 格式 | 命令 | 底层 | 依赖 |
|------|------|------|------|
| PDF | `/ea doc x.pdf` | pdfplumber（需文本层）| pip: `pdfplumber` |
| Word `.docx` | `/ea doc x.docx` | `zipfile` + `xml.etree` | **无** |
| Excel `.xlsx` | `/ea doc x.xlsx` | `zipfile` + `xml.etree` | **无** |

> DOCX / XLSX 本质是 zip + XML，用标准库直解，不引入 `python-docx` / `openpyxl`。
> 只有 PDF 需要装包。

## 安装

```bash
py .../scripts/deps_check.py --detect     # 检测（DOCX/XLSX 恒为就绪）
py .../scripts/deps_check.py --install    # 装 pdfplumber
```

唯一 pip 依赖见 `requirements.txt`（`pdfplumber`）。

## 用法

```bash
py doc_reader.py <文件>                 # Markdown（表格超 20 行截断）
py doc_reader.py <文件> --outline       # 只输出结构大纲（标题 + 每节摘录 + 表格尺寸）
py doc_reader.py <文件> --full          # 不截断（表格 + 每表 1000 行上限全部解除）
py doc_reader.py <文件> --tables        # 只输出表格
py doc_reader.py <文件> --pages 1-20    # PDF 页范围（也支持 1,5,7-9）
py doc_reader.py <文件> --sheet Sheet1  # 只解析指定工作表（可重复）
py doc_reader.py <文件> --json          # 结构化 JSON（供 AI 消费）
py doc_reader.py <文件> --out out.md    # 同时写入文件
py doc_reader.py <文件> --no-cache      # 忽略缓存
py doc_reader.py --scan <目录>          # 列出目录下可识别文档
```

## 缓存

解析结果（结构化 JSON）按**源文件内容哈希**缓存在 `<STATE_DIR>/docs/<name>-<sha8>.json`。
内容变则文件名变，无需失效判断；`--no-cache` 绕过。无状态目录（`.ea/` / `.em/`）时自动跳过缓存。

## 边界

| 情况 | 行为 |
|------|------|
| 扫描件 PDF（无文本层）| **明确报错**，提示改用 `Read` 工具走视觉路径（不做 OCR）|
| 部分页无文本层 | 警告 + 跳过该页文本，其余正常解析 |
| `.doc` / `.xls`（老二进制格式）| 报错 + 提示另存为 `.docx` / `.xlsx` |
| `.pptx` / `.ppt` | 报错 + 提示另存为 PDF |
| `.csv` / `.txt` / `.md` | 提示直接用 `Read` 工具 |
| 加密 / 损坏文件 | 报错，不静默失败 |
| 超大工作表 | 默认每表截断 1000 行 + 警告（`--full` 解除）|
| 超大表格预览 | 默认展示 20 行 + 省略提示（`--full` 解除）|

## 无文档自测

```bash
py doc_reader.py --scan <任意目录>            # 扫描模式不需要任何文档
py doc_reader.py <一份 .docx 或 .xlsx>        # DOCX/XLSX 零依赖，装上就能跑
```

## 相关文件

- `scripts/doc_reader.py` — 主入口（解析 + 渲染 + 缓存）
- `scripts/deps_check.py` — 依赖探测（`/ea setup` 调用）
- `requirements.txt` — pip 依赖（仅 `pdfplumber`）
- `../../commands/doc.md` — `/ea doc` 命令定义
