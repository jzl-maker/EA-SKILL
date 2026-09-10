# EA-SKILL 使用说明

嵌入式 AI 开发管家 —— 面向单片机/嵌入式工程（Keil/CubeMX/ESP-IDF/PlatformIO/Arduino）的 AI 辅助开发工具。让 Claude Code 直接接管"编译、烧录、串口监控、调试、寄存器查询、固件资源分析"等环节。

## 系统要求

| 项 | 要求 |
|----|------|
| 操作系统 | Windows 10/11（工具链基于 Windows） |
| Claude Code | 已安装（CLI / VSCode 扩展） |
| Python | 3.9+，**用 `py` 启动器**（`python` 可能是 Store 假别名） |
| Keil MDK | 编译必需（`uv4`），SVD 寄存器地图自动从 Pack 目录发现 |
| OpenOCD | 烧录必需（`/ea setup` 可自动下载 xpack 版） |
| J-Link（可选） | RTT 日志 / 断点 / 源码级调试 |
| ST-Link / DAP-Link（可选） | OpenOCD 后端免断点内存监控 |

## 安装

```bash
# 1. 把 skill 复制到 Claude 技能目录
cp -r ea-skill ~/.claude/skills/ea-skill

# 2. 运行环境初始化（注册工具路径 + 写入 CLAUDE.md 触发器）
/ea setup
```

`/ea setup` 完成三件事：
1. 追加脚本执行权限到 `~/.claude/settings.json`
2. 探测并注册工具路径（OpenOCD / Keil UV4 / J-Link / Logic2）到 `%APPDATA%/ea_skill/config.json`
3. 把「编译/烧录/串口/RTT/断点/SVD/需求分析/文档识别」等关键词 → 命令文档指针表写入 `~/.claude/CLAUDE.md`（动态按需加载，不污染常驻上下文）

## 命令表（20 个）

| 命令 | 用途 |
|------|------|
| `/ea setup` | 环境/工具初始化（权限、工具路径、CLAUDE.md 触发器） |
| `/ea init` | 新项目初始化（建 context 骨架 + 基线快照） |
| `/ea si` | 存量项目接入（代码审计 + context + 基线快照） |
| `/ea rec` | 恢复项目（state + context 摘要，只读） |
| `/ea new` | 新功能开发（需求澄清 → 轻档/标准档，嵌入式硬件维度） |
| `/ea doc` | 文档识别（PDF / Word / Excel → Markdown / JSON） |
| `/ea test` | 生成测试用例 + 测试代码（unit / board） |
| `/ea build` | Keil 编译 |
| `/ea flash` | OpenOCD 烧录 |
| `/ea serial` | 串口监控（CLI/MCP） |
| `/ea debug` | 调试（J-Link RTT/断点/内存/寄存器；OpenOCD ST-Link/DAP 免断点监控） |
| `/ea svd` | SVD 寄存器地图（外设/位域/枚举 + 免断点读值） |
| `/ea la` | Saleae 逻辑分析仪（抓取/协议解码） |
| `/ea scope` | 示波器（Rigol SCPI 抓取/测量；正点原子 DS100 CSV 解析） |
| `/ea size` | 固件资源分析（Flash/RAM/栈 + 超限预警） |
| `/ea map` | Map 文件解析（对象排行/分段分布/符号映射） |
| `/ea verify` | 步骤验证（HVR + 四连 + 保护区审计） |
| `/ea record` | 输出修改记录（文件 + diff + 测试证据） |
| `/ea stat` | 状态查看（默认极简，`-v` 全景） |
| `/ea help` | 帮助 |

## 快速上手

一个典型调试闭环（AI 自主执行，你在旁边确认）：

```bash
# 1. 接入工程
/ea si .                # 存量项目接入 → 生成 context.md + 基线快照

# 2. 开发+验证
/ea doc 需求规格.docx                      # 可选：先解析需求/引脚表/手册文档
/ea new "增加一个 500ms 定时翻转 LED"      # 需求澄清 → 规划
/ea new "加 CAN 上报" --plan-only          # 只做需求澄清+方案确认，不写代码
/ea test unit ehtimer_tick                 # 纯函数单测
/ea build                                  # Keil 编译
/ea flash                                  # OpenOCD 烧录
/ea serial --log .em/logs/uart.log         # 串口监控（另开终端抓日志）

# 3. 调试（代码跑哪了 / 变量对不对）
/ea debug --rtt start                      # RTT 日志
/ea debug --mem _TimeCount_10ms --watch 5  # 变量持续采样
/ea svd --chip N32G4FR --read RCC CR       # 免断点读寄存器 + 位域解码
/ea size --project .                       # Flash/RAM 用量 + 超限预警

# 4. 记录
/ea record s1                              # 输出修改记录
```

## 真机验证记录

| 能力 | 硬件 | 状态 |
|------|------|------|
| `/ea flash` 烧录 | ST-Link / DAP | ✅ 已验证 |
| `/ea scope` 波形抓取/测量 | Rigol DS1074Z | ✅ 已验证（Vpp/频率/周期/占空比） |
| `/ea scope` CSV 解析 | 正点原子 DS100 | ✅ 已验证（自动提取采样率/探头倍率） |
| `/ea svd` 寄存器地图 | N32G4FR（Keil Pack SVD） | ✅ 已验证（derivedFrom 继承/位域/枚举） |
| `/ea debug` OpenOCD 免断点 | 待 ST-Link/DAP 实机 | ⏳ 待验证 |

## 常见问题

| 问题 | 解决 |
|------|------|
| `python` 命令不可用 | 用 `py`（Windows Python launcher） |
| 中文输出乱码 | 脚本已内置 UTF-8 reconfigure；命令行直跑加 `PYTHONIOENCODING=utf-8` |
| 找不到 OpenOCD/J-Link | 运行 `/ea setup` 注册工具路径 |
| Keil 源码中文乱码 | 工程源文件为 GB2312 编码，禁止 UTF-8 编辑器直接改中文 |
| 调试 halt 后复位 | 设备带 IWDG 看门狗，缩短 `--run-ms` 或用 OpenOCD 后端免断点读 |

## 开发

- 源码在 `tools/<name>/scripts/*.py` + `commands/<cmd>.md`
- 修改后部署：`cp -r ea-skill/. ~/.claude/skills/ea-skill/`，用 `diff -rq` 验证
- 新增命令流程：写脚本 → 写命令文档 → 更新 `SKILL.md` 命令表 → 更新触发器模板 → 部署
