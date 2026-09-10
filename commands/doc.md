# 命令: /ea doc (文档识别)

## 功能
把 PDF / Word(`.docx`) / Excel(`.xlsx`) 转成 Markdown 或 JSON，供 AI 直接消费。
用于识别：芯片手册/数据手册、需求规格文档、引脚分配表、寄存器表、测试报告、协议说明。

> **只做解析，不做 OCR**。扫描件（无文本层的 PDF）明确报错，不做图片文字识别。

## 触发
```
/ea doc <路径>                 # Markdown（默认，表格超 20 行截断）
/ea doc <路径> --outline       # 只输出结构大纲（快速判断文档讲什么）
/ea doc <路径> --full          # 不截断
/ea doc <路径> --tables        # 只输出表格
/ea doc <路径> --pages 1-20    # PDF 页范围（也支持 1,5,7-9）
/ea doc <路径> --sheet Sheet1  # 只解析指定工作表（可重复）
/ea doc <路径> --json          # 结构化 JSON
/ea doc <路径> --out <文件>    # 同时写入文件
/ea doc --scan <目录>          # 列出目录下可识别文档
```

## 执行流程

1. **【依赖检查】** 首次运行前确认 `pdfplumber`（PDF 需要；DOCX/XLSX 零依赖）：
   ```bash
   py ~/.claude/skills/EA-SKILL/tools/doc-reader/scripts/deps_check.py --detect
   ```
2. **【路径确认】** 确认目标文件存在、扩展名受支持
3. **【调用工具】**
   ```bash
   py ~/.claude/skills/EA-SKILL/tools/doc-reader/scripts/doc_reader.py <路径> [参数]
   ```
4. **【解读】** 按用户意图消费结果（见下方「串联场景」）

## 支持矩阵

| 格式 | 解析方式 | 依赖 | 说明 |
|------|---------|------|------|
| PDF | `pdfplumber` | pip | **需文本层**；含表格提取（`--pages` 分段）|
| Word `.docx` | `zipfile` + XML | 无 | 保留标题层级 + 表格 |
| Excel `.xlsx` | `zipfile` + XML | 无 | 多工作表 + sharedStrings + 内联字符串 |

**不支持**（明确报错并给替代方案）：

| 格式 | 提示 |
|------|------|
| 扫描件 PDF | 无文本层 → 改用 `Read` 工具走视觉识别 |
| `.doc` / `.xls` | 老二进制格式 → 另存为 `.docx` / `.xlsx` |
| `.pptx` / `.ppt` | 另存为 PDF 后再 `/ea doc` |
| `.csv` / `.txt` / `.md` | 直接用 `Read` 工具读取 |

## 输出格式

```markdown
# <文件名>

> 来源: `<路径>`
> 格式: pdf | 解析: 2026-09-10 09:45
> pages_total=120 | pages_parsed=120 | pages_with_text=118 | tables=3
> ⚠️ 2 页无文本层（可能是扫描页/纯图页），已跳过其文本

## 第 1 页
<正文>

| 列1 | 列2 |
|---|---|
```

- 标题行 `> ` 元信息 + 警告，便于 AI 判断可信度（有几页没解析到）
- `--json` 输出 `{path, format, meta, warnings, sections[]}`，`sections[].kind ∈ heading/paragraph/page/table/sheet`

## 缓存

- 解析结果缓存到 `<STATE_DIR>/docs/<name>-<sha8>.json`，哈希取自**源文件内容** → 内容变则自动失效
- 命中时输出里带 `⚠️ （缓存命中: ...）`，可据此判断是否需要 `--no-cache`
- 无状态目录（未 `/ea init`）时自动跳过缓存，不影响使用

## 串联场景（本命令的主要价值）

| 场景 | 用法 |
|------|------|
| 从需求文档起步 | `/ea doc req.docx` → `/ea new <要点> --doc req.docx`（进阶段 0 澄清）|
| 引脚分配表 → 硬件对齐 | `/ea doc pins.xlsx` 结果直接喂 `new-standard.md` 的「硬件对齐」表 |
| 芯片手册 → 寄存器核对 | `/ea doc RM.pdf --pages 300-340` 配合 `/ea svd` 定位位域 |
| 协议说明 → 通信维度 | `/ea doc protocol.pdf` 结果填入 `requirement.md` 的「通信协议」行 |
| 测试报告归档 | `/ea record` 时附文档引用 |

## 边界 / 红线

- **不改源文件**：本命令只读。缓存写入 `<STATE_DIR>/docs/`（UTF-8 新建文件，**不触碰 GBK 编码红线**）
- **不做 OCR**：扫描件不猜、不糊弄，直接报错让用户换路径
- **大文档分段**：单次解析超长文档会占用大量上下文 → 先 `--outline` 看结构，再 `--pages` / `--sheet` 精读
- **表格截断要说明**：默认 20 行预览 + 1000 行/表上限，截断时输出里有明确提示，汇报给用户时不要省略该提示

## 依赖安装

```bash
# 探测
py ~/.claude/skills/EA-SKILL/tools/doc-reader/scripts/deps_check.py --detect
# 安装（仅 pdfplumber；DOCX/XLSX 无需安装）
py ~/.claude/skills/EA-SKILL/tools/doc-reader/scripts/deps_check.py --install
```

## 相关文件
- `tools/doc-reader/scripts/doc_reader.py` — 解析工具
- `tools/doc-reader/scripts/deps_check.py` — 依赖探测
- `tools/doc-reader/README.md` — 工具说明
- `commands/new.md` — `--doc` 参数接入需求澄清
- `workflows/req-clarify.md` — 阶段 0 需求澄清（文档输入源）
