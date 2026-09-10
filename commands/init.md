# 命令: /ea init (新项目初始化)

## 功能
从零开始新嵌入式项目，生成 `<STATE_DIR>/` 标准结构 + `context.md` 骨架 + 基线快照。

## 触发
```
/ea init <项目名称>
/ea init <项目名称> --chip GD32F407VET6   # 直接指定芯片（跳过选择）
```

## 执行流程

1. **【目录检测】** 当前目录已有 `.ea/` 或 `.em/` → 提示「项目已初始化」+ 建议 `/ea rec`
2. **【状态目录】** 新建 `.ea/`（纯嵌入式，无需类型判定）
   ```
   .ea/
   ├── state.md          # 最小状态（当前步骤 S0）
   ├── project.json      # { type:"embedded", name, created, chip, toolchain, interface }
   ├── context.md        # 工程上下文骨架
   ├── project-spec.md   # 步骤表（空骨架）
   ├── decisions.md  problem-log.md  approvals.md
   ├── sessions/  discussion/  checkpoints/  history/  logs/
   └── records/  tests/
   ```
3. **【芯片选择】** 走 `workflows/chip-learning.md`：
   - 读 `~/.claude/chips.json`（最近使用 + 内置列表）
   - 用户选择或自定义输入
   - 外设列表（GPIO/UART/I2C/SPI/CAN/Timer/ADC...）写入 context
4. **【工具链确认】** 确认编译/烧录/调试工具链（Keil target / OpenOCD interface / J-Link 设备名），写入 `project.json`
5. **【基线快照】** `python ~/.claude/skills/EA-SKILL/tools/shared/project_guard.py --snapshot`
   - 对保护区文件 + 当前源文件做 SHA-256 → 写入 context.md 基线区
   - 新项目此时无源文件，基线为空表，`si` 接入老工程时才真正生效
6. **【写 state.md】** 当前步骤 S0，下一步动作：`/ea new <功能描述>`
7. **【输出初始化报告】**

## 输出格式

```
✅ 嵌入式项目初始化完成

项目: <名称>
芯片: GD32F407VET6 (GigaDevice)  外设: GPIO/UART/I2C/SPI/CAN/Timer/ADC
工具链: Keil UV4 + OpenOCD(stlink) + J-Link
状态目录: .ea/

📁 已创建: state.md project.json context.md project-spec.md decisions.md
          problem-log.md approvals.md + sessions/discussion/checkpoints/history/logs/records/tests

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
下一步:
  /ea new <功能描述>    # 进入新功能开发
  /ea setup            # 若工具路径未配置
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## 设计原则
- ✅ 纯嵌入式：无 general/embedded 类型判定
- ✅ context 先行：新项目即建上下文骨架，后续命令按需补充
- ✅ 基线快照：从第一天起保护区可审计

## 相关文件
- `commands/setup.md` — 工具环境初始化
- `workflows/chip-learning.md` — 芯片选择/学习
- `workflows/context-build.md` — 上下文构建流程
- `tools/shared/project_guard.py` — 基线快照/审计
- `templates/project.json` — 项目元数据模板
