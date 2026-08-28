# EA-SKILL — 嵌入式 AI 开发工具

**EA-SKILL（Embedded Artificial Intelligence）**：嵌入式 + AI，对新老嵌入式工程做**新功能开发、测试、调试验证**。

定位收敛为纯嵌入式：无通用项目分支，无插件加载器，命令扁平化。

---

## 核心能力

| 能力 | 入口 |
|------|------|
| 工程上下文感知 | `/ea init`（新）/ `/ea si`（存量）→ `context.md` |
| 新功能规划 | `/ea new`（轻档 / 标准档，含嵌入式硬件对齐） |
| 测试双产出（用例文档 + 可运行测试代码） | `/ea test unit|board` |
| 开发 / 编译 / 烧录 / 串口 / 调试 | `/ea build flash serial debug` |
| SVD 寄存器地图（位域/枚举 + 免断点读值） | `/ea svd` |
| 仪器测量（逻辑分析仪 / 示波器） | `/ea la scope` |
| 固件资源分析 / Map 解析 | `/ea size map` |
| 验证四连（编译→烧录→J-Link 运行验证→串口） | `/ea verify` |
| 修改记录 + 上下文增量学习 | `/ea record` |
| 状态查看 / 恢复 | `/ea stat rec` |
| 环境初始化 | `/ea setup` |

## 红线规则

- ❌ 禁止 AI 改保护区文件：`startup_*.s`、中断向量表、`*.sct`/`*.ld`/`*.icf`、`system_*.c` —— 必须用户显式 `/ea approve`
- ❌ 禁止整文件重写；默认**增量修改**，所有修改给出 diff
- ❌ 禁止 `git push` / 公开发布
- ⚠️ Keil 源文件 GB2312，禁止用 UTF-8 编辑器改中文

## 开发闭环

```
感知(si/init→context) → 规划(new) → 测试(test) → 开发(build/debug)
→ 验证(verify: 编译→烧录→J-Link→串口+保护区审计) → 记录(record) → 迭代
```

## 安装

```bash
# 拷贝到 Claude skills 目录
cp -r ea-skill ~/.claude/skills/ea-skill
# 初始化环境（探测 UV4/OpenOCD/J-Link，注册到 CLAUDE.md）
/ea setup
```

## 目录结构

```
ea-skill/
├── SKILL.md              # 入口：19 命令表 + 红线 + 状态目录
├── commands/             # 19 个命令文档（AI 按需读取）
├── workflows/            # 验证四连 / HVR / 新功能 / 上下文构建
├── templates/            # context/approvals/测试/HVR 等模板
├── tools/
│   ├── build-keil/  flash-openocd/  serial-mcp/  serial-monitor/  jlink-debug/
│   ├── svd/              # svd（SVD 寄存器地图：位域/枚举/免断点读值）
│   ├── instrument/       # la（Saleae 逻辑分析仪）/ scope（Rigol 示波器）
│   ├── resource/         # size（Flash/RAM 分析）/ map（Map 文件解析）
│   ├── test-runner/      # test_gen.py（单测/板级用例生成）
│   └── shared/           # tool_config / detect_tools / project_guard / register_claude_md
└── mcp-servers/          # serial-mcp 注册配置
```

## 状态目录

`<STATE_DIR>/`：`.ea/` 。含 `context.md`、`approvals.md`、`records/`、`logs/`、`sessions/`、`discussion/` 等。
