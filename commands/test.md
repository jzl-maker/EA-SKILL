# 命令: /ea test (测试生成与执行)

## 功能
**测试双产出**：每个测试都生成「测试用例文档 + 可运行测试代码」。
- `unit`：纯函数 host 单元测试（轻量自研：桩函数 + 断言 + main，零框架依赖）
- `board`：板级工装验收测试（用例文档 + 板载测试固件/驱动脚本）

## 触发
```
/ea test unit <文件>               # 对指定 .c 文件的纯函数生成单测
/ea test board <功能描述>          # 对功能/需求生成板级工装用例
```

⚠️ `unit` **只收 1 个位置参数**（`target`）。写 `/ea test unit <文件> <函数名>` 会
`error: unrecognized arguments` —— 工具没有筛选函数名的参数。要只测某个函数，先跑一次
拿到「候选函数」列表，生成后在 `.c` 里删掉其余用例即可。

## 单元测试（unit）

### 流程
1. **【读 context】** 确认工程上下文
2. **【扫描目标】** `tools/test-runner/scripts/test_gen.py unit <文件>` 提取函数签名，识别纯函数（无硬件外设调用）
3. **【生成双产出】** 写入 `<STATE_DIR>/tests/unit/`：
   - **测试代码** `<name>.c`：桩函数（mock `GPIO_SetBits`/`USART_SendData` 等硬件调用）+ 断言测试体 + `main` runner
   - **测试用例文档** `<name>.md`：用例ID / 输入 / 预期输出 / 覆盖点
4. **【补全骨架】** ⚠️ **这一步不能跳** —— 见下「生成的骨架必然编译不过」
5. **【host 编译运行】** 补全后再编译（Windows 用 mingw `gcc`；也可用 `arm-none-eabi-gcc`）
6. **【输出结果】** 每个用例 PASS/FAIL

### ⚠️ 生成的骨架必然编译不过 —— 它是半成品，不是成品

`test_gen.py` 产出的是**待补全的骨架**：它只认函数名，不解析签名，所以断言一律写成
`f(0)`。拿未经补全的产物直接 `--cc gcc` 必然失败，这是**预期行为**，不是工具坏了：

| 现象 | 原因 |
|------|------|
| `implicit declaration of 'X'` | 骨架把原型声明写成了注释（`/* int target_func(int a, int b); */`），等你替换 |
| `too few arguments to function 'X'` | 实参一律按 `f(0)` 生成，**无视真实签名**（3 个形参也只给 1 个实参） |
| `void value not ignored` | `void` 函数也被套进了 `ASSERT_EQ(...)` |
| `fatal error: XXX.h: No such file` | 编译命令**不带 `-I`**，头文件搜索路径不从 `.uvprojx` 继承 |
| `undefined reference` | `--cc gcc` 只编译生成的 `.c`，未链接被测源；且 `static` 函数跨 TU 本就链不上 |

**补全清单**（AI 按需求逐项做，做完再编译）：

1. 换成真实的 `#include` 与**函数原型声明**（骨架里是注释）
2. 按**真实签名**填实参，`void` 函数改用 `ASSERT_TRUE` 或直接调用
3. 补 `-I <头文件目录>`，并把被测 `.c` 一起编译；`static` 函数改为在骨架中 `#include` 被测源
4. 逐个核对 `/* TODO: 核对输入/预期 */`，给出真实输入与预期输出

### 调用方式
```bash
# 生成（不编译）
python <SKILL>/tools/test-runner/scripts/test_gen.py unit <目标.c>
# 产物: <STATE_DIR>/tests/unit/<name>.c + <name>.md

# 补全骨架后再编译运行（示例：头文件目录 + 被测源一并给上）
gcc <STATE_DIR>/tests/unit/<name>.c <被测源.c> -I <头文件目录> -o /tmp/ut && /tmp/ut
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

   ⚠️ **`<case>` 是 ASCII 短标识，不是功能描述原文**。描述里带中文/`/ : *` 时，
   `test_gen.py` 只取其中的 ASCII 片段（`om100 指纹模块上电自检` → `om100`），
   并补一个**内容哈希后缀**避免同前缀用例互相覆盖（→ `om100_c5fbe6`）。
   理由：产出的 `.c` 是要加进 Keil 工程编译的，非 ASCII 路径在 Keil / CI / 别人机器上
   都是已知的坑。**描述本身不丢**：它照写在用例文档标题和 `.c` 的文件头注释里。
   `.c` 按 **GBK** 写出（Keil 默认 GB2312 读源码，UTF-8 中文在 IDE 里是乱码）。
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
