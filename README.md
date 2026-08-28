<div align="center">

# EA-SKILL · 嵌入式 AI 开发管家

**让 Claude Code 直接接管你的单片机** —— 编译、烧录、串口、调试、寄存器查询、示波器测量，全部一句话搞定。

```
输入 /ea new "给 LED 加 500ms 定时翻转"   →   自动完成 规划 → 编译 → 烧录 → 验证
```

```
[build]  Keil MDK 编译通过 ✅   (0 errors, 0 warnings)
[flash]  OpenOCD 烧录完成 ✅   (N32G4FR, 12.4s)
[scope]  CHAN1: 1.00kHz 方波, Vpp 3.08V, 50% 占空比 ✅   ← 真机实测
[svd]    RCC.CR = 0x0100XX00 → HSEON 已置位 ✅
```

</div>

## 🎯 为什么值得用

| 痛点 | EA-SKILL 解决 |
|------|----------------|
| 编译烧录要在 Keil/OpenOCD 之间来回点 | 一句话 `/ea build flash`，AI 全程接管 |
| 查寄存器要翻 1500 页参考手册 | `/ea svd` 免断点读值 + 位域/枚举自动解码 |
| 调变量要打断点 halt 住 CPU，中断就停 | **OpenOCD 免断点内存监控**，运行中实时采样 |
| 看波形要手动抓示波器再导出分析 | `/ea scope` 直接 SCPI 抓取 + 测量 + 出图 |
| 固件超 Flash/RAM 了才知道 | `/ea size` 编译即报占用率 + 超限预警 |

## ✨ 差异化亮点

- 🔓 **免断点调试**：OpenOCD TCL 协议直接读内存，CPU 不 halt，中断/外设持续运行
- 📖 **SVD 寄存器地图**：自动发现 Keil Pack SVD，derivedFrom 继承展开，免查手册
- 🔬 **仪器直连**：Rigol 示波器（SCPI 抓取/测量）+ Saleae 逻辑分析仪 + 正点原子 DS100 CSV 解析
- 🧱 **硬件红线保护**：禁止 AI 改启动文件/链接脚本/中断向量表，需 `/ea approve` 显式授权
- 🛡️ **AI 行为约束**：增量修改默认给 diff，防 AI 整文件重写搞坏工程

## 🚀 快速上手

**安装（一句话）：** 在 Claude Code 或 Cline 等支持 Agent Skills 的客户端对话中，直接输入：
```
帮我安装 https://github.com/jzl-maker/EA-SKILL.git 的 skill
```
AI 会自动克隆仓库、安装到技能目录并初始化环境。然后接入你的工程：

```
/ea setup        # 环境/工具初始化（权限、工具路径、CLAUDE.md 触发器）
/ea si .          # 存量项目接入 → AI 生成上下文
/ea new "..."     # 说人话描述功能，AI 规划并开发
```

## 🔌 大模型适配

skill 的加载机制由**宿主客户端**提供，与模型本身解耦——DeepSeek 及任何 Anthropic 兼容端点的模型都能直接使用 EA-SKILL。

| 模型 × 客户端 | 支持 | 说明 |
|---------------|------|------|
| Claude × Claude Code | ✅ 开箱即用 | 原生 skill 机制 |
| **DeepSeek × Claude Code** | ✅ | Claude Code 设置 `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`，`/ea setup` 全流程不变 |
| DeepSeek × Cline | ✅ | Cline 配 DeepSeek 端点 + 安装到 `.claude/skills`，setup 微调（见下）|
| 其他 Anthropic 兼容模型 × agent 客户端（Gemini CLI / Cursor）| ✅ | 需客户端支持 Agent Skills，setup 微调 |
| 纯聊天客户端（Copilot Chat / Chatbox / 网页版）| ❌ | 无「读文档 → 执行命令」的 agent 工具机制 |

> ✅ 已用 DeepSeek（deepseek-v4）真机跑通编译 / 烧录 / 示波器 / SVD 全流程。

**换客户端时 setup 微调**（skill 本体无需改动）：
- **权限**：跳过 `~/.claude/settings.json` 那步（Cline 等会自动弹权限确认），或换成该客户端的权限格式
- **触发器**：改跑 `py register_claude_md.py --target <该客户端记忆文件>`（如 `.cursor/rules` / `AGENTS.md`），或手动写等效规则

## 📦 命令总览（19 个）

| 分类 | 命令 |
|------|------|
| 环境 / 项目 | `/ea setup` `/ea init` `/ea si` `/ea rec` |
| 开发 / 测试 | `/ea new` `/ea test` |
| 构建 / 烧录 | `/ea build` `/ea flash` |
| 调试 / 寄存器 | `/ea debug` `/ea svd` |
| 仪器 | `/ea la`（逻辑分析仪）`/ea scope`（示波器） |
| 资源分析 | `/ea size`（Flash/RAM/栈）`/ea map`（.map 解析） |
| 验证 / 记录 | `/ea verify` `/ea record` `/ea stat` `/ea help` |

## ✅ 真机验证

| 能力 | 硬件 | 状态 |
|------|------|------|
| 烧录 | ST-Link / DAP | ✅ |
| 波形抓取/测量 | Rigol DS1074Z | ✅ |
| CSV 解析 | 正点原子 DS100 | ✅ |
| SVD 寄存器地图 | N32G4FR | ✅ |
| OpenOCD 免断点 | ST-Link/DAP | ⏳ 待实机 |

## 🔧 系统要求

Windows 10/11 + Claude Code + Python 3.9+（`py`）+ Keil MDK + OpenOCD（J-Link / ST-Link 可选）

## 🧱 目录结构

```
ea-skill/
├── SKILL.md          # 入口（命令表 + 红线）
├── commands/         # 19 个命令文档（AI 按需读取，不污染上下文）
├── workflows/        # 验证四连 / HVR / 新功能流程
├── tools/            # build / flash / serial / debug / svd / instrument / resource ...
└── mcp-servers/      # 串口 MCP
```

## 📄 License

[MIT](LICENSE) © 2026 jzl-maker

---

**Star 一下 ⭐，让嵌入式开发更 AI。**
