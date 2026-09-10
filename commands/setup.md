# 命令: /ea setup (环境/工具初始化)

## 功能
初始化 EA-SKILL 开发环境，一次配置长期有效：
1. 更新 Claude 权限配置（允许脚本执行 / 状态文件编辑，禁止危险操作）
2. 探测并注册工具路径（Keil UV4 / OpenOCD / J-Link）
3. 自动下载 OpenOCD（如果未安装）
4. 注册全局 CLAUDE.md 触发器（编译/烧录/串口/RTT/断点/需求分析/文档识别 关键词 → 命令文档指针）

## 触发
```
/ea setup
```

## 执行流程

### 步骤 1: 更新权限配置
自动更新 `~/.claude/settings.json`，追加脚本执行权限：

```json
"permissions": {
  "allow": [
    "Read", "Glob", "Grep", "Search",
    "Bash(git:status)", "Bash(git:diff)",
    "Bash(ls:*)",
    "Edit(**/.ea/**/*.md)", "Write(**/.ea/**/*.md)",
    "Edit(**/.em/**/*.md)", "Write(**/.em/**/*.md)",
    "Bash(python:*)",
    "Bash(python */EA-SKILL/tools/build-keil/*.py)",
    "Bash(python */EA-SKILL/tools/flash-openocd/*.py)",
    "Bash(python */EA-SKILL/tools/serial-monitor/*.py)",
    "Bash(python */EA-SKILL/tools/jlink-debug/*.py)",
    "Bash(py */EA-SKILL/tools/svd/*.py)",
    "Bash(py */EA-SKILL/tools/instrument/*.py)",
    "Bash(py */EA-SKILL/tools/doc-reader/*.py)",
    "Bash(py */EA-SKILL/tools/resource/*.py)",
    "Bash(py -m pip install *)",
    "Bash(python */EA-SKILL/tools/shared/*.py)"
  ],
  "ask": [
    "Write(**/.ea/discussion/**/*.md)",
    "Edit(**/.ea/discussion/**/*.md)"
  ],
  "deny": [
    "Bash(rm:*)", "Bash(sudo:*)", "Bash(git push:*)"
  ]
}
```

### 步骤 2: 探测工具路径
```bash
python EA-SKILL/tools/shared/detect_tools.py
```
找到的工具自动注册到 `%APPDATA%/ea_skill/config.json`（工作区级 `.ea_skill.json` 可覆盖）。

### 步骤 3: 注册工具
| 工具 | config key | 必须 | 说明 |
|------|-----------|------|------|
| OpenOCD | `openocd` | ✅ 必须 | 烧录工具（未安装则自动下载）|
| Keil UV4 | `uv4` | ✅ 必须 | 编译工具（用户手动指定）|
| J-Link | `jlink` | 可选 | 烧录 + 调试工具（debug 命令需要）|
| Logic 2 | `logic2` | 可选 | Saleae 逻辑分析仪软件（la 命令需要，探测到即注册）|

未找到的工具：OpenOCD → 自动下载；Keil UV4 → 提示用户手动指定；其余 → 提示手动指定。

### 仪器工具（/ea la /ea scope，可选）

`la`（Saleae 逻辑分析仪）与 `scope`（Rigol 示波器）依赖第三方 pip 包与硬件后端，按需启用：

```bash
# 探测依赖与后端（Logic 2 软件 / 端口 / VISA 资源）
py ~/.claude/skills/EA-SKILL/tools/instrument/scripts/deps_check.py --detect
# 缺 pip 包时安装（重依赖，确认后执行）
py ~/.claude/skills/EA-SKILL/tools/instrument/scripts/deps_check.py --install
```

- **pip 依赖**：`logic2-automation` / `pyvisa` / `pyvisa-py` / `numpy` / `matplotlib`
- **Logic 2 软件**（非 pip）：Saleae 官网下载或 `winget install Saleae.Logic2`；探测到即注册 `logic2` 工具路径
- **Rigol 示波器**：USB 直连需 USB-TMC 驱动（Zadig 装 WinUSB），或用系统 VISA 后端
- 无硬件自测：`/ea la --simulate`（模拟设备）、`/ea scope --parse-tmc`（合成 TMC 块）

### 文档识别工具（/ea doc，可选）

`doc` 解析 PDF / Word / Excel。**只有 PDF 需要 pip 包**，DOCX / XLSX 走标准库零依赖：

```bash
# 探测（DOCX/XLSX 恒为就绪）
py ~/.claude/skills/EA-SKILL/tools/doc-reader/scripts/deps_check.py --detect
# 装 pdfplumber（PDF 解析需要）
py ~/.claude/skills/EA-SKILL/tools/doc-reader/scripts/deps_check.py --install
```

- **pip 依赖**：`pdfplumber`（仅 PDF）
- **DOCX / XLSX**：标准库 `zipfile` + `xml.etree`，无需安装
- 扫描件 PDF（无文本层）不支持，提示改用 `Read` 工具视觉识别

### 步骤 4: 自动下载 OpenOCD
OpenOCD 是烧录必须工具，未安装时自动下载 xpack 发行版：
```
https://github.com/xpack-dev-tools/openocd-xpack/releases
```

### 步骤 5: 注册全局 CLAUDE.md 触发器
把「编译/烧录/串口/RTT/断点/需求分析/文档识别」关键词 → 命令文档的指针表幂等写入 `~/.claude/CLAUDE.md`，实现按需动态加载：

```bash
python EA-SKILL/tools/shared/register_claude_md.py
```

| 状态 | 行为 |
|------|------|
| 已存在 `## EA-SKILL 嵌入式工具（动态加载）` section | 跳过（幂等）|
| 已存在但缺 section | 追加 |
| 不存在 | 创建并写入模板 |

仅检查（不写入）：`python .../register_claude_md.py --check`

> ⚠️ 原则：CLAUDE.md 只写指针（≤10 行），不灌用法；Claude 按需 Read 命令文档。

## Git 权限

`setup` 同时建议追加 Git 白名单：
```json
[
  "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
  "Bash(git add:*)", "Bash(git commit:*)", "Bash(git tag:*)"
]
```
**禁止**：`Bash(git push:*)`（AI 只提议 commit，push 由用户手动执行）。

## Python 环境检查

`setup` 检查 Python 与依赖（pyserial / mcp / instrument 可选 / doc-reader 可选），缺失则 `pip install`。

## 输出示例

```
🔧 EA-SKILL 环境初始化

[1/5] 更新权限配置...   ✅
[2/5] 探测工具...
  ✅ openocd: D:/OpenOCD/xpack-openocd-0.12.0-7/bin/openocd.exe
  ✅ uv4: C:/Keil_v5/UV4/UV4.exe
  ✅ jlink: C:/Program Files/SEGGER/JLink/JLink.exe
[3/5] 注册工具路径...   ✅
[4/5] 检查 OpenOCD...   ✅
[5/5] 注册 CLAUDE.md 触发器...   ✅

━━━━━━━━━━━━━━━━━━━━━━━━
✅ 初始化完成！下一步：/ea init <name> 或 /ea si [path]
```

## 相关文件
- `tools/doc-reader/` — 文档识别（PDF/Word/Excel，`/ea doc` 用）
- `tools/shared/detect_tools.py` — 工具探测
- `tools/shared/tool_config.py` — 工具路径持久化（`%APPDATA%/ea_skill/config.json`）
- `tools/shared/register_claude_md.py` — CLAUDE.md 触发器注册
- `templates/claude-md-snippet.md` — 触发器模板
