# 命令: /ea help (帮助)

## 功能
显示命令列表或指定命令的详细帮助。

## 触发
```
/ea help [命令]
```

## 无参数时
显示命令表（20 个）：

```
嵌入式 AI 开发管家 — /ea 命令

环境/项目:
  setup            环境工具初始化（权限 / 工具路径 / CLAUDE.md 触发器）
  init <name>      新项目初始化（建 context + 基线快照）
  si [path]        存量项目接入（代码审计 + context + 基线快照）
  rec [name]       恢复项目（state + context 摘要）
  stat [-v/steps/next]  状态查看

开发流程:
  new <描述>       新功能开发（需求澄清 → 轻档 quick-plan / 标准档 brainstorm+milestones）
    --plan-only      只做需求澄清与方案确认，产出 requirement.md 后停下
    --no-clarify     跳过需求澄清（需求已明确）
    --quick/--deep   覆盖澄清深度（快 ≤3 题 / 深 9 维度全扫）
    --doc <路径>     从文档提取需求（先跑 /ea doc）
  doc <路径>       文档识别（PDF / Word / Excel → Markdown，--outline/--full/--json）
  test unit <文件|函数>   生成并运行纯函数 host 单测（用例 .md + 代码 .c）
  test board <功能>       生成板级工装验收用例（用例 .md + 板载测试固件/驱动脚本）

嵌入式工具:
  build            编译（Keil UV4）
  flash            烧录（OpenOCD）
  serial           串口监控（CLI/MCP）
  debug            调试（J-Link RTT 取证/断点/内存/寄存器/复位放行；OpenOCD 免停机监控）
  svd              SVD 寄存器地图（外设 / 位域 / 枚举 + 免断点读值）
  la               Saleae 逻辑分析仪（抓取 / 协议解码）
  scope            Rigol 示波器（波形抓取 / 测量）
  size             固件资源分析（Flash/RAM/栈 + 超限预警）
  map              Map 文件解析（对象排行 / 分段分布 / 符号映射）

验证/记录:
  verify s<N>      步骤验证（HVR + 编译→烧录→J-Link 验证→串口 + 保护区审计）
  record s<N>      输出修改记录（文件清单 + diff + 测试证据）
  help [命令]      查看帮助
```

## 有参数时
显示指定命令的详细帮助：

```
环境/项目:
/ea help setup      # 环境初始化
/ea help init       # 新项目初始化
/ea help si         # 存量接入
/ea help rec        # 恢复项目
/ea help stat       # 状态查看

开发流程:
/ea help new        # 新功能开发（需求澄清 + 两档）
/ea help doc        # 文档识别（PDF / Word / Excel）
/ea help test       # 测试生成（unit / board）

嵌入式工具:
/ea help build      # 编译（Keil UV4）
/ea help flash      # 烧录（openocd / jlink-native 双通道）
/ea help serial     # 串口监控
/ea help debug      # 调试（J-Link / OpenOCD 双后端）
/ea help svd        # SVD 寄存器地图
/ea help la         # 逻辑分析仪
/ea help scope      # 示波器
/ea help size       # 固件资源分析
/ea help map        # Map 文件解析

验证/记录:
/ea help verify     # 验证流程 + 保护区审计
/ea help record     # 修改记录
/ea help help       # 本帮助
```

> 清单必须覆盖全部命令 —— 上面「无参数时」列了几条，这里就得有几条。
> 漏掉的那几条会让人以为命令不存在。

## 红线提醒
任何命令执行中都需遵守 SKILL.md 红线：保护区文件禁随意改、老工程增量修改、所有修改给 diff、GBK 编码。

## 相关文件
- `commands/<cmd>.md` — 各命令完整定义
- `workflows/` — 工作流细则
- `SKILL.md` — 总览 + 红线规则
