# 命令: /ea test (测试生成与执行)

## 功能
**测试双产出**：每个测试都生成「测试用例文档 + 可运行测试代码」。
- `unit`：纯函数 host 单元测试（轻量自研：桩函数 + 断言 + main，零框架依赖）
- `board`：板级工装验收测试（用例文档 + 板载测试固件/驱动脚本）

## 触发
```
/ea test unit <文件>               # 对指定 .c 文件的纯函数生成单测
/ea test unit <文件> <函数名>      # 只测某个函数
/ea test board <功能描述>          # 对功能/需求生成板级工装用例
```

## 单元测试（unit）

### 流程
1. **【读 context】** 确认工程上下文
2. **【扫描目标】** `tools/test-runner/scripts/test_gen.py unit <文件>` 提取函数签名，识别纯函数（无硬件外设调用）
3. **【生成双产出】** 写入 `<STATE_DIR>/tests/unit/`：
   - **测试代码** `<name>.c`：桩函数（mock `GPIO_SetBits`/`USART_SendData` 等硬件调用）+ 断言测试体 + `main` runner
   - **测试用例文档** `<name>.md`：用例ID / 输入 / 预期输出 / 覆盖点
4. **【host 编译运行】** `--cc gcc` 直接编译运行（Windows 用 mingw `gcc`；也可用 `arm-none-eabi-gcc` 纯函数编译）
5. **【输出结果】** 每个用例 PASS/FAIL；生成器产出骨架断言，AI 负责按需求核对输入/预期后再判

### 调用方式
```bash
python ~/.claude/skills/EA-SKILL/tools/test-runner/scripts/test_gen.py \
  unit <目标.c> --cc gcc
# 产物: <STATE_DIR>/tests/unit/<name>.c + <name>.md
```

### 硬件耦合处理
- 目标函数直接调用硬件寄存器/外设库且无法桩化 → 提示「该逻辑适合板级测试」→ 走 `board`
- 测试代码中桩函数与真实实现冲突时，用 `#ifdef EA_TEST` 包裹被测模块

## 板级工装测试（board）

### 流程
1. **【读 context】** 芯片/外设/调试接口（串口端口、GPIO、J-Link）
2. **【生成用例】** 套 `templates/board-test-templates.md` 模板库（上电/LED/按键/串口/外设响应/异常复位），写入 `<STATE_DIR>/tests/board/`：
   - **用例文档** `<case>.md`：前置条件 / 操作步骤 / 预期结果 / 判定方法
   - **测试代码**：板载测试固件 `<case>_test.c`（调被测逻辑，结果按协议串口输出 `[TEST] PASS/FAIL`）或 host 驱动脚本 `<case>_driver.py`（用 serial/debug 工具自动执行 + 判定）
3. **【执行】** AI 驱动：
   - 板载固件：编译→烧录→串口收集 `[TEST]` 输出
   - host 脚本：直接驱动 serial/debug 读结果/寄存器
4. **【判定】** 按用例预期逐条判定，写入用例文档「实际结果」

### 输出示例（用例文档）
```markdown
# 板级用例: 按键 KEY0 触发功能
- 前置: 上电，串口 COM5@115200
- 步骤: 按下 KEY0 → 观察串口输出
- 预期: 串口出现 `[KEY0] pressed`，LED 翻转
- 判定: 串口断言 + 物理观察 LED
```

## 设计原则
- ✅ 用例 + 代码双产出：文档给人看、代码给机器跑
- ✅ 零框架依赖：单测用 assert+桩，不引入第三方测试框架
- ✅ 板级贴近真实：工装用例用串口/调试接口做判定锚点

## 相关文件
- `tools/test-runner/scripts/test_gen.py` — 测试生成器
- `templates/unit-test-runner.c` — 单测骨架模板
- `templates/board-test-case.md` — 板级用例模板
- `templates/board-test-templates.md` — 验收用例模板库
- `commands/build.md` / `flash.md` / `serial.md` / `debug.md` — 执行依赖
