---
name: ea-skill
description: 嵌入式 AI 开发管家（EA-SKILL）— 面向单片机/嵌入式工程（Keil/CubeMX/ESP-IDF/PlatformIO/Arduino）的需求澄清与方案确认、新功能开发、单元测试、板级工装测试、编译烧录、J-Link 调试与验证。当用户要对嵌入式新老项目开发新功能、加外设、改业务逻辑、排查硬件 bug、验证固件、生成测试用例，或需求描述不清晰需要讨论确认方案、需要读取 PDF/Word/Excel 文档（芯片手册/需求规格/引脚表）时使用；对话提到 编译/烧录/串口/RTT/断点/内存监视/需求分析/文档识别 等嵌入式关键词时按需加载。
version: 1.1.0
---

# EA-SKILL（Embedded AI）

> 嵌入式 + AI：对新老嵌入式工程做「新功能开发 → 测试 → 调试 → 验证 → 记录」闭环。
> 命令统一前缀 `/ea`。

你接收到的参数：`$ARGUMENTS`

## 快速开始

```
1. /ea setup          # 首次：环境/工具初始化（权限 / 工具路径 / CLAUDE.md 触发器）
2. /ea init <名>      # 新项目    或  /ea si [路径]   存量项目接入（建 context + 基线）
3. /ea doc <文档>     # 可选：先解析需求/引脚/手册文档（PDF / Word / Excel）
4. /ea new <功能描述>  # 新功能开发（需求澄清 → 轻 / 标准 两档）
5. /ea test ...       # 生成测试用例 + 可运行测试代码（unit / board）
6. /ea verify s<N>    # 验证：编译 → 烧录 → J-Link 运行验证 → 串口
7. /ea record s<N>    # 输出修改记录（文件 + diff + 测试证据）
```

**首次进入项目**：

| 场景 | 命令 |
|------|------|
| 全新项目 | `/ea init <name>` |
| 存量项目（无状态目录）| `/ea si [path]` |
| 恢复已有项目 | `/ea rec` |

## 命令表（20 个）

| 命令 | 用途 | 文档 |
|------|------|------|
| `/ea setup` | 环境/工具初始化（权限、工具路径、CLAUDE.md 触发器）| [setup.md](commands/setup.md) |
| `/ea init` | 新项目初始化（建 context 骨架 + 基线快照）| [init.md](commands/init.md) |
| `/ea si` | 存量项目接入（代码审计 + context + 基线快照）| [si.md](commands/si.md) |
| `/ea rec` | 恢复项目（state + context 摘要，只读）| [rec.md](commands/rec.md) |
| `/ea new` | 新功能开发（需求澄清 → 两档分流，嵌入式硬件维度）| [new.md](commands/new.md) |
| `/ea doc` | 文档识别（PDF / Word / Excel → Markdown / JSON）| [doc.md](commands/doc.md) |
| `/ea test` | 生成测试用例 + 测试代码：`unit` / `board` | [test.md](commands/test.md) |
| `/ea build` | Keil 编译 | [build.md](commands/build.md) |
| `/ea flash` | OpenOCD 烧录 | [flash.md](commands/flash.md) |
| `/ea serial` | 串口监控（CLI/MCP）| [serial.md](commands/serial.md) |
| `/ea debug` | 调试（J-Link RTT 取证/断点/内存/寄存器/复位放行；OpenOCD ST-Link/DAP 免停机监控）| [debug.md](commands/debug.md) |
| `/ea svd` | SVD 寄存器地图（外设/位域/枚举 + 免断点读值）| [svd.md](commands/svd.md) |
| `/ea la` | Saleae 逻辑分析仪（抓取/协议解码）| [la.md](commands/la.md) |
| `/ea scope` | Rigol 示波器（波形抓取/测量）| [scope.md](commands/scope.md) |
| `/ea size` | 固件资源分析（Flash/RAM/栈 + 超限预警）| [size.md](commands/size.md) |
| `/ea map` | Map 文件解析（对象排行/分段分布/符号映射）| [map.md](commands/map.md) |
| `/ea verify` | 步骤验证（HVR + 四连 + 保护区审计）| [verify.md](commands/verify.md) |
| `/ea record` | 输出修改记录（文件 + diff + 测试证据）| [record.md](commands/record.md) |
| `/ea stat` | 状态查看（默认极简，`-v` 全景）| [stat.md](commands/stat.md) |
| `/ea help` | 帮助 | [help.md](commands/help.md) |

> **子命令路由约定**：AI 执行任一命令时，读取 `commands/<cmd>.md`，严格按文档执行，不自行发明流程。

## 红线规则（必须遵守）

1. **保护区文件禁止随意修改**：`startup_*.s`、中断向量表、链接脚本（`*.sct`/`*.ld`/`*.icf`）、`system_*.c` 核心初始化。必须修改时：先给方案+理由 → 用户显式 `/ea approve <文件> <理由>` → 最小 diff。
2. **老工程默认增量修改**：禁止整文件重写、禁止全盘重写工程；单文件改动 ≥30% 或触碰核心逻辑 → 先给 diff 计划，用户确认。
3. **所有修改给出 diff**：git 项目用 `git diff`；非 git 项目用 [project_guard.py](tools/shared/project_guard.py) 基线比对。
4. **先读 context 再动手**：`new`/`debug`/`verify`/`record` 执行前先读 `<STATE_DIR>/context.md`。
5. **GBK 编码**：Keil 工程 `.c`/`.h` 为 GB2312 编码，读取按 `encoding="gbk"`，修改用字节级操作，禁止 UTF-8 编辑器直接改中文（会整文件乱码）。
6. **Git push 禁止**：AI 只提议 commit，`git push` 必须用户手动执行。

## 状态目录布局

```
<STATE_DIR>/   ← .ea/ (优先) 或 .em/ (兼容老项目)
├── state.md           # 最小状态（≤50 行，rec 只读）
├── project.json       # { type:"embedded", name, chip, toolchain, interface }
├── context.md         # ⭐ 工程上下文（芯片/外设/模块地图/保护区/基线哈希）
├── project-spec.md    # 步骤表
├── decisions.md       # 决策日志
├── problem-log.md     # 问题追踪
├── approvals.md       # 保护区改动审批日志
├── sessions/          # 每会话一文件
├── discussion/        # 讨论目录（需求澄清 requirement.md / brainstorm / milestones）
├── docs/              # 文档解析缓存（/ea doc 输出，按内容哈希）
├── checkpoints/       # HVR 文件
├── history/           # 归档
├── logs/              # 串口/编译日志
├── records/           # 修改记录（/ea record 输出）
└── tests/             # 单测代码 + 板级用例（/ea test 输出）
```

状态目录定位：

```python
def get_state_dir(root):
    for d in (".ea", ".em"):
        p = os.path.join(root, d)
        if os.path.isdir(p):
            return p
    return None
```

## 详细文档

- `commands/` — 20 个命令定义
- `workflows/` — 工作流：context-build（上下文构建）、chip-learning（芯片学习）、req-clarify（需求澄清）、new-light / new-standard（两档）、verify-flow（编译→烧录→J-Link→串口）、hvr-workflow
- `templates/` — 模板：state / context / project / hvr / approvals / requirement / 单测与板级测试
- `tools/` — 工具：build-keil / flash-openocd / serial-mcp / serial-monitor / jlink-debug / svd / instrument（la+scope）/ doc-reader（pdf+docx+xlsx）/ resource（size+map）/ shared / test-runner
- `mcp-servers/` — serial-mcp MCP server 配置

查看详细：`/ea help <命令>`
