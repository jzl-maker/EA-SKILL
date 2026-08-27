# 命令: /ea debug (调试)

## 功能
自主调试四件套，用于「代码跑哪了 / 变量值对不对 / 根因是什么」。**双后端**：`--backend jlink`（默认）或 `--backend openocd`（ST-Link/DAP 用，**无停机监控**）。

| 能力 | 命令 | 底层工具 | 依赖 |
|------|------|----------|------|
| RTT 打印日志 | `--rtt` | JLinkRTTLogger.exe（channel 0） | J-Link |
| 断点定位 | `--bp <函数名/地址>` | JLink Commander SetBP + halt | J-Link |
| 内存监视 | `--mem <变量名/地址>` | JLink mem32/mem8 或 OpenOCD TCL mdw/mdh/mdb | J-Link 或 OpenOCD |
| 寄存器 | `--regs` | JLink regs 或 OpenOCD TCL `reg` | J-Link 或 OpenOCD |
| 源码级调试（可选） | `--gdb` | JLinkGDBServerCL + arm-none-eabi-gdb | + GNU 工具链 |

**零第三方 Python 依赖**：只调用 J-Link / OpenOCD 自带可执行文件。
`py` 是 Windows Python launcher（`python` 可能是 Store 假别名，勿用）。

> **后端选择**：有 J-Link 用默认 jlink（支持全功能）；只有 ST-Link/DAP 用 `--backend openocd`（内存/寄存器**免断点实时监控**——TCL `mdw` 运行中直读，不 halt CPU，适合 `--watch` 持续采样看变量变化；`--rtt/--bp/--gdb` 仅 jlink 后端支持）。

## 调用方式

```bash
# ① 探测工具链（确认 JLink/RTTLogger/GDBServer 是否可用）
py ~/.claude/skills/ea-skill/tools/jlink-debug/scripts/jlink_debug.py --detect

# ② 启动 RTT 日志抓取（先启动 logger，再触发复位，避免错过启动消息）
py .../jlink_debug.py --rtt start --log .em/logs/rtt.log
py .../jlink_debug.py --rtt stop
py .../jlink_debug.py --rtt show --log .em/logs/rtt.log --tail 50

# ③ 断点定位：函数跑哪了？（.map 符号 → SetBP → go → halt → 报告 PC/寄存器）
py .../jlink_debug.py --bp Display_BootDoraemon --run-ms 2000
py .../jlink_debug.py --bp 0x08001d28

# ④ 内存监视：变量值是多少？（符号名或地址）
py .../jlink_debug.py --mem _TimeCount_10ms            # 默认 4×32bit
py .../jlink_debug.py --mem _TimeCount_10ms --watch 5  # 持续采样 5 次
py .../jlink_debug.py --mem 0x20020228 --count 8 --width 1

# ⑤ 读寄存器
py .../jlink_debug.py --regs

# ⑥ 源码级调试（可选，需 arm-none-eabi-gdb）
py .../jlink_debug.py --gdb --elf build/app.axf --gdb-script debug.gdb

# ⑦ ST-Link/DAP 后端：内存/寄存器免断点实时监控（OpenOCD，不 halt CPU）
py .../jlink_debug.py --backend openocd --mem _TimeCount_10ms --watch 10 --interval 0.5
py .../jlink_debug.py --backend openocd --regs
# --ocd-if stlink（默认）| cmsis-dap；--ocd-target 指定 target（默认 target/stm32f4x.cfg 通用 Cortex-M4）
# OpenOCD 自动拉起/复用（先查 6666 端口，已开则复用不打扰用户进程）

# 辅助：符号表 / 地址解析（AI 先核对映射再下断点）
py .../jlink_debug.py --list-symbols Display
py .../jlink_debug.py --resolve Display_BootDoraemon
```

## 符号解析

工具自动扫描工作区 `**/*.map`（Keil Listing 产物）解析 Image Symbol Table 段（含 Global + Local Symbols，static 变量也能按名监视）：

```
Display_BootDoraemon   0x08001d29   Thumb Code    size=80  ehimage.o(...)
_TimeCount_10ms        0x20020228   Data          size=4   ehtimer.o(.data)
```

- **函数**地址自动去除 Thumb bit（`0x08001d29 → 0x08001d28` 真实地址）
- **变量**可直接按名字读（.data 段在 RAM，如 `0x2002xxxx`）
- 找不到符号 → 自动扫描失败时用 `--map <路径>` 显式指定

## 自主调试闭环（AI 执行流程）

> 目标：用户描述一个 bug，AI 自主用 RTT + 断点 + 内存定位根因。

```
1. RTT 日志快照
   --rtt start → 请用户/触发复位 → --rtt stop → --rtt show
   从日志看现象、找异常时刻 T

2. 断点定位代码位置
   --resolve <可疑函数> 核对地址
   --bp <可疑函数> --run-ms <T>     # 跑到 T ms 停在断点
   看 PC/SP/LR 判断执行流是否按预期进入

3. 内存变量验证
   --mem <关键变量> --watch 5        # 持续采样看变化
   --regs                           # 看寄存器现场

4. 分析根因 → 给用户结论 + 修改建议
   （把 PC、变量值、寄存器现场作为证据写入 HVR/结论）
```

## 参数表

| 参数 | 必须 | 说明 |
|------|------|------|
| `--rtt start/stop/show` | 是（三选一动作） | RTT 日志抓取管理 |
| `--bp <函数/地址>` | 是（断点） | 断点定位 + 运行 N ms + halt + 报告 |
| `--mem <变量/地址>` | 是（内存） | 读内存变量 |
| `--regs` | 是（寄存器） | 读 CPU 寄存器 |
| `--gdb` | 是（源码调试） | 源码级调试（可选增强） |
| `--detect` | 是（探测） | 探测工具链 |
| `--map` | 否 | 显式指定 .map 符号文件 |
| `--run-ms` | 否 | `--bp` 运行毫秒数（默认 2000） |
| `--count` | 否 | `--mem` 读取字数（默认 4） |
| `--width` | 否 | `--mem` 宽度：4=32bit 1=8bit（默认 4） |
| `--watch` | 否 | `--mem` 采样次数（默认 1，>1 持续监视） |
| `--log` | 否 | `--rtt` 日志路径（默认 rtt.log） |
| `--device` | 否 | 芯片型号（默认 N32G4FRRE） |
| `--if` | 否 | 接口（默认 SWD） |
| `--speed` | 否 | SWD 速度 kHz（默认 4000） |
| `--backend {jlink,openocd}` | 否 | 后端：默认 jlink；ST-Link/DAP 用 openocd |
| `--ocd-if` | 否 | OpenOCD 接口配置：stlink/cmsis-dap/jlink（默认自动探测）|
| `--ocd-target` | 否 | OpenOCD target 配置（默认 `target/stm32f4x.cfg`，N32G4FR 等 Cortex-M4 通用）|
| `--ocd-port` | 否 | OpenOCD TCL 端口（默认 6666）|
| `--interval` | 否 | `--watch` 采样间隔秒（默认 0.3）|
| `--dry-run` | 否 | 只打印生成的脚本/计划，不执行 |
| `--json` | 否 | 结构化 JSON 输出（供 AI 解析） |

## ⚠️ 注意事项

1. **IWDG 看门狗**：本类设备常带 250ms 看门狗。`--bp` 是「跑 N ms 后 halt」，**halt 期间不喂狗会触发复位**，读 PC/内存要快；若 halt 后立即复位，缩短 `--run-ms` 或改用 `--mem`（mem 命令自动短时 halt+resume）。
2. **RTT 时序**：`--rtt start` 必须**先于复位**，否则设备已跑过启动日志，错过启动消息。
3. **符号歧义**：模糊匹配可能命中多个符号（如 `Display` → 6 个），先用 `--resolve` 核对再下断点。
4. **断点上限**：J-Link 硬件断点约 6 个，设置过多会失败。
5. **gdb 源码级调试**：已随 GNU Arm Toolchain 14.2 安装（`D:\ruanjian\arm-gnu-toolchain\bin`，已入用户 PATH）。未检测到时自动降级提示，`--bp/--mem/--regs` 不受影响。
6. **VFP 寄存器警告**：Cortex-M4F 的 gdb 读到 fpscr 后尝试读浮点扩展寄存器（s0-s31）时，JLink GDBServer 响应解析会报 `Expected an decimal digit`——**无害**，核心寄存器（pc/lr/sp/xpsr/r0-r12）已全部正确读出，可忽略。
7. **OpenOCD 无停机**：`--backend openocd` 的 `--mem/--regs` 用 TCL `mdw`/`reg` 运行中直读，**不 halt CPU**（喂狗/中断/外设不受影响）——优于 jlink 后端的 halt+resume，特别适合 `--watch` 持续采样。OpenOCD 自动拉起（先探测已开端口则复用，不打扰用户进程），退出时仅停自己拉起的进程。
8. **OpenOCD 无 N32 target**：OpenOCD 0.12 无 N32 厂商 target，N32G4FR 是 Cortex-M4，用 `target/stm32f4x.cfg` 通用配置即可（`--ocd-target` 可换）。`--rtt/--bp/--gdb` 仅 jlink 后端支持，openocd 下会明确报错。

## 常见错误

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| 未找到 JLink.exe | 工具路径未注册 | 运行 `/ea setup` 注册，或 `--jlink` 指定路径 |
| 无法连接目标 | 调试器未接/芯片供电/被占用 | 检查 USB 连接、供电、关闭 Keil 调试 |
| 找不到符号 | 无 .map 或名字不符 | `--list-symbols` 查看，`--map` 指定文件 |
| RTT 无日志 | logger 后于复位启动 / 通道错 | 先 start 再复位；`--channel 0` 默认 |
| halt 后复位 | IWDG 看门狗 | 缩短 `--run-ms`，或用 `--mem` 短读 |
| gdb 不可用 | arm-none-eabi-gdb 不在 PATH | 确认 `D:\ruanjian\arm-gnu-toolchain\bin` 在 PATH；或重开终端刷新环境 |
| 未找到 openocd | 工具未注册/不在 PATH | `/ea setup` 注册，或加入 PATH |
| OpenOCD 启动失败 | 接口不匹配/调试器被占用 | `--ocd-if stlink/cmsis-dap` 指定；关闭 Keil 调试 |
| OpenOCD 只支持 mem/regs | 用了 --rtt/--bp/--gdb | 换 `--backend jlink` 或改 `--mem/--regs` |

## 源文件编码注意

涉及修改工程 `.c`/`.h` 时：本类 Keil 工程源文件为 **GB2312（GBK）** 编码。读取用 `encoding="gbk"`，修改用字节级 Python 操作，禁止 UTF-8 编辑器直接改中文（会整文件乱码）。详见 `build.md` 的编码章节。

## 相关文件
- `~/.claude/skills/ea-skill/tools/jlink-debug/scripts/jlink_debug.py` - 调试工具脚本
- `commands/build.md` - 编译说明
- `commands/flash.md` - 烧录说明
- `commands/serial.md` - 串口监控说明