# 命令: /ea si (存量项目接入)

## 功能
分析现有嵌入式工程，重建项目状态：建立 `<STATE_DIR>/`（`.ea/` 或沿用老 `.em/`）+ 完整 `context.md` + 基线快照，让老项目进入可被 AI 增量开发/调试的状态。

## 触发
```
/ea si [路径]
```
不带路径时使用当前目录。

## 执行流程

1. **【进入目录】** 定位项目根
2. **【状态目录】** `get_state_dir()`：已有 `.ea/`/`.em/` → 沿用并合并；否则新建 `.ea/`
3. **【代码审计 + 上下文构建】** 走 `workflows/context-build.md`：
   - 芯片识别：`system_*.c` / `startup_*.s` → chips.json 学习（复用 `workflows/chip-learning.md` 正则）
   - 外设识别：搜索 `gpio_init`/`usart_init`/`i2c_init` 等关键词
   - 构建入口：扫描 `*.uvprojx`/`*.uvproj`/`*.ioc`/`sdkconfig`/`platformio.ini`
   - **模块地图**：main / 驱动层 / 应用层 / 中断处理 / printf 重定向 / LED·按键调试接口
   - **既有约定**：编码（GB2312 检测）、命名规范、架构约束
   - **保护区清单**：`startup_*.s`、向量表、`*.sct`/`*.ld`/`*.icf`、`system_*.c`
   - 全部写入 `<STATE_DIR>/context.md`
4. **【基线快照】** `python ~/.claude/skills/ea-skill/tools/shared/project_guard.py --snapshot`
   - 保护区 + 关键源文件 SHA-256 → context.md 基线区
   - **老工程保护从此生效**：之后任何改动都能 diff / 审计
5. **【git 检查】** `git log -1` 读最后一次提交信息写入 state.md
6. **【生成规格】** `<STATE_DIR>/project-spec.md` 步骤表（从代码推断已完成步骤）+ `state.md`
7. **【输出审计报告】**

## 输出格式

```
🔍 代码审计完成

📟 芯片: GD32F407VET6 (GigaDevice)  外设: GPIO/UART/I2C/SPI/CAN/Timer
🔧 工程: Keil (.uvprojx)  target=GD32F407  linker=xxx.sct
🗺 模块: main → task/Display/Drv_UART...  printf→串口3  调试: LED_PC13/按键KEY0
🛡 保护区: startup_gd32f407.s / system_gd32f4xx.c / xx.sct  已加基线
✅ 已完成: S1 板级驱动  S2 显示   ...
🔄 当前状态: 主循环 + 看门狗 250ms；上次提交: <git log 一行>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
下一步: /ea rec  或  /ea new <功能描述>
```

## 设计原则
- ✅ 老工程零破坏：不改任何源文件，只建状态/上下文
- ✅ 增量开发前提：context + 基线让后续所有改动可 diff、保护区可审计
- ✅ 既有 `.em/` 项目兼容：沿用不迁移

## 相关文件
- `workflows/context-build.md` — 上下文感知构建（本命令核心）
- `workflows/chip-learning.md` — 芯片识别/学习
- `tools/shared/project_guard.py` — 基线快照
- `templates/context.md` — context 模板
