# 命令: /ea debug (调试)

## 功能
自主调试五件套，用于「代码跑哪了 / 变量值对不对 / 日志说了什么 / 根因是什么」。**双后端**：`--backend jlink`（默认）或 `--backend openocd`（ST-Link/DAP 用，**无停机监控**）。

| 能力 | 命令 | 底层工具 | 是否 halt 目标 | 后端 |
|------|------|----------|----------------|------|
| RTT 日志（**含历史/取证**）| `--rtt snapshot` | JLink `savebin` 直读 RAM | **不 halt** | jlink |
| RTT 日志（只能抓新数据）| `--rtt start/stop/show` | JLinkRTTLogger.exe | 不 halt | jlink |
| 断点定位 | `--bp <函数名/地址>` | JLink Commander SetBP + halt | halt | jlink |
| 内存监视 | `--mem <变量名/地址>` | JLink mem32/mem8 | halt（`--no-halt` 可免）| jlink |
| 内存监视（免停机）| `--mem --no-halt` | JLink `savebin` 后台读 | **不 halt** | jlink |
| 内存监视（免停机）| `--mem/--regs` | OpenOCD TCL `mdw`/`reg` | **不 halt** | openocd |
| 寄存器 | `--regs` | JLink regs / OpenOCD `reg` | halt（ocd 免）| 两者 |
| 复位+放行 | `--reset-run` | JLink Commander `r`+`g` | 复位后自由运行 | jlink |
| 源码级调试（可选）| `--gdb` | JLinkGDBServerCL + arm-none-eabi-gdb | 由 gdb 控制 | jlink |

**零第三方 Python 依赖**：只调用 J-Link / OpenOCD 自带可执行文件。
`py` 是 Windows Python launcher（`python` 可能是 Store 假别名，勿用）。

> **后端选择**：有 J-Link 用默认 jlink（支持全功能）；只有 ST-Link/DAP 用 `--backend openocd`（内存/寄存器免断点实时监控，TCL `mdw` 运行中直读不 halt CPU；`--rtt/--bp/--gdb/--reset-run` 仅 jlink 后端支持）。

## 调用方式

```bash
BJ="py ~/.claude/skills/EA-SKILL/tools/jlink-debug/scripts/jlink_debug.py"

# ① 探测工具链（确认 JLink/RTTLogger/GDBServer 是否可用）
$BJ --detect

# ② RTT 取证：直读 RAM，拿回「已经发生」的历史日志（推荐）
$BJ --rtt snapshot                              # 控制块地址自动从 .map 的 _SEGGER_RTT 解析
$BJ --rtt snapshot --cb-addr 0x200014DC         # .map 里没有时手工指定
$BJ --rtt snapshot --out .ea/logs/rtt.log       # 原始字节落盘
$BJ --rtt snapshot --json                       # 结构化输出

# ③ RTT 轮询：目标跑人工交互测试，过程中反复采样
$BJ --rtt snapshot --duration 420 --interval 5 --out .ea/logs/rtt.log
$BJ --rtt snapshot --poll 20 --interval 5

# ④ 复位 + 放行（配合人工按压测试；也可加在 ② 前面先复位）
$BJ --reset-run
$BJ --rtt snapshot --reset-run --duration 420 --interval 5

# ⑤ RTT 实时日志（只能抓 attach 之后的新数据，须先 start 再复位）
$BJ --rtt start --log .ea/logs/rtt.log
$BJ --rtt stop
$BJ --rtt show --log .ea/logs/rtt.log --tail 50
$BJ --rtt show --log .ea/logs/rtt.log --encoding gbk

# ⑥ 断点定位：函数跑哪了？（.map 符号 → SetBP → go → halt → 报告 PC/寄存器）
$BJ --bp Display_BootDoraemon --run-ms 2000
$BJ --bp 0x08001d28

# ⑦ 内存监视：变量值是多少？
$BJ --mem _TimeCount_10ms                  # 默认 4×32bit，halt→读→go
$BJ --mem _TimeCount_10ms --no-halt --watch 10  # 不打断正在跑的目标
$BJ --mem _TimeCount_10ms --watch 5
$BJ --mem 0x20020228 --count 8 --width 1

# ⑧ 读寄存器
$BJ --regs

# ⑨ 源码级调试（可选，需 arm-none-eabi-gdb）
$BJ --gdb --elf build/app.axf --gdb-script debug.gdb

# ⑩ ST-Link/DAP 后端：内存/寄存器免断点实时监控（OpenOCD，不 halt CPU）
$BJ --backend openocd --mem _TimeCount_10ms --watch 10 --interval 0.5
$BJ --backend openocd --regs
# --ocd-if stlink（默认）| cmsis-dap；--ocd-target 指定 target（默认 target/stm32f4x.cfg 通用 Cortex-M4）
# OpenOCD 自动拉起/复用（先查 6666 端口，已开则复用不打扰用户进程）

# 辅助：符号表 / 地址解析（AI 先核对映射再下断点）
$BJ --list-symbols Display
$BJ --resolve Display_BootDoraemon
```

## RTT 采集：三种方式的可靠性差异（重要）

需要 RTT 日志时，**先按用途选方式**——它们的能力差别很大，选错会拿到不可信的证据：

| 方式 | 能拿历史数据 | 丢行 | 适用场景 |
|------|-------------|------|----------|
| `--rtt snapshot`（savebin 直读 RAM）| ✅ **全部** | **逐字节精确** | ✅ **取证 / 回读已发生的日志** |
| `--rtt start`（JLinkRTTLogger）| ❌ 只能抓 attach 之后 | 高吞吐时会丢 | 先 start 再复位，抓启动日志 |
| `JLinkGDBServerCL -RTTTelnetPort` | ❌ 只能抓 attach 之后 | **随机丢行**（实测 RUN1 丢 `[12]`、RUN2 丢开头两行）| ⚠️ **不可用于取证** |

**要证据就用 `--rtt snapshot`**。它与 JLinkRTTLogger 的根本差别：Logger 是「订阅」——只收到订阅之后目标推来的数据；snapshot 是**直接读目标 RAM 里的 SEGGER RTT 环形缓冲**，所以已发生的日志也在里面。

原理（工具已封装，仅供理解）：

```
1. savebin 读 SEGGER_RTT_CB 控制块（acID[16] + NumUp + NumDown + aUp[0]）
2. 校验 cb[0:10] == b"SEGGER RTT"     ← 挡住 savebin 错位读出的垃圾
3. 从 aUp[0] 取 pBuffer / SizeOfBuffer / WrOff / RdOff
4. 按 WrOff 取环形缓冲 [rd, wr) 区间，处理回绕
5. 轮询模式下记住上一轮 WrOff，只取新增段
```

**丢行判定**：轮询时若两次采样之间目标写出量接近整圈（`--interval` 太长 / 输出太快），工具会告警并计入疑似丢失字节。缩短 `--interval` 即可。
另注意：本工具**只读不回写 RdOff**，目标侧缓冲写满后会按 RTT 跳过策略丢弃数据，因此高频输出场景请缩短采样间隔。

### 为什么可以「随便暂停读 RAM」——JLink Commander 的非侵入性

三点前提（都经实测确认，写进这里以免被误解而不敢用）：

1. `JLink.exe -AutoConnect 1 -CommanderScript f` 里的 `connect` **既不复位也不 halt** 目标（输出 `CPU is not halted !`）。很多人以为 `connect` 会复位目标从而不敢用这条路——恰恰相反，它是唯一的取证通道。
2. `savebin` 走 **AHB-AP 后台访问**，对目标**零干扰**：目标全速运行（含中断、外设、喂狗）时也能反复读。
3. 前提是 IWDG 已被调试冻结——见下方「注意事项 1」。

工具已将这些固化：`--rtt snapshot` / `--mem --no-halt` 全程不 halt、不复位。

## 符号解析

工具自动扫描工作区 `**/*.map`（Keil Listing 产物）解析 Image Symbol Table 段（含 Global + Local Symbols，static 变量也能按名监视）：

```
Display_BootDoraemon   0x08001d29   Thumb Code    size=80  ehimage.o(...)
_TimeCount_10ms        0x20020228   Data          size=4   ehtimer.o(.data)
```

- **函数**地址自动去除 Thumb bit（`0x08001d29 → 0x08001d28` 真实地址）
- **变量**可直接按名字读（.data 段在 RAM，如 `0x2002xxxx`）
- **RTT 控制块** `_SEGGER_RTT` 也在此解析，`--rtt snapshot` 默认用它，不必手填地址
- 找不到符号 → 自动扫描失败时用 `--map <路径>` 显式指定

## 自主调试闭环（AI 执行流程）

> 目标：用户描述一个 bug，AI 自主用 RTT + 断点 + 内存定位根因。

```
1. RTT 证据（选对方式！）
   日志已发生 / 要取证  → --rtt snapshot
   要从复位抓启动日志    → --rtt start  → 请用户复位 → --rtt stop → --rtt show
   目标正在跑人工测试    → --rtt snapshot --duration N --interval 5
   从干净状态起跑        → --rtt snapshot --reset-run --duration N
   从日志看现象、找异常时刻 T

2. 断点定位代码位置
   --resolve <可疑函数> 核对地址
   --bp <可疑函数> --run-ms <T>     # 跑到 T ms 停在断点
   看 PC/SP/LR 判断执行流是否按预期进入

3. 内存变量验证
   --mem <关键变量> --watch 5          # 默认 halt→读→go
   --mem <关键变量> --no-halt --watch 5  # 目标在跑交互测试时用这个，不打断
   --regs                             # 看寄存器现场

4. 分析根因 → 给用户结论 + 修改建议
   （把 PC、变量值、寄存器现场作为证据写入 HVR/结论）
```

> ⚠️ 第 3 步在**目标正在跑需要人工操作的测试**时，必须用 `--no-halt`。默认的 `halt → 读 → go`
> 会中断那次测试（哪怕只有几十毫秒）。

## 参数表

| 参数 | 必须 | 说明 |
|------|------|------|
| `--rtt start/stop/show/snapshot` | 是（RTT 动作）| `snapshot`=直读 RAM 取证；其余=JLinkRTTLogger |
| `--bp <函数/地址>` | 是（断点） | 断点定位 + 运行 N ms + halt + 报告 |
| `--mem <变量/地址>` | 是（内存） | 读内存变量 |
| `--regs` | 是（寄存器） | 读 CPU 寄存器 |
| `--reset-run` | 是（复位放行）| 复位并放行目标（`r`+`g`）；也可作为 `--rtt snapshot` 的修饰 |
| `--gdb` | 是（源码调试） | 源码级调试（可选增强） |
| `--detect` | 是（探测） | 探测工具链 |
| `--no-halt` | 否 | `--mem` 用 savebin 后台读，不 halt CPU |
| `--cb-addr <地址>` | 否 | RTT 控制块地址（缺省从 .map 的 `_SEGGER_RTT` 解析）|
| `--poll N` | 否 | `--rtt snapshot` 轮询次数（缺省 1 次快照）|
| `--duration S` | 否 | `--rtt snapshot` 持续采集秒数（优先于 `--poll`）|
| `--encoding` | 否 | RTT 日志解码：`auto`(默认，先 UTF-8 再回落 GBK)/`gbk`/`utf-8` |
| `--out <文件>` | 否 | `--rtt snapshot` 原始字节写入文件 |
| `--force` | 否 | `--rtt start` 时强制清理已占用的 JLinkRTTLogger |
| `--map` | 否 | 显式指定 .map 符号文件 |
| `--run-ms` | 否 | `--bp` 运行毫秒数（默认 2000） |
| `--count` | 否 | `--mem` 读取字数（默认 4） |
| `--width` | 否 | `--mem` 宽度：4=32bit 1=8bit（默认 4） |
| `--watch` | 否 | `--mem` 采样次数（默认 1，>1 持续监视） |
| `--interval` | 否 | 采样间隔秒（`--mem --watch` 默认 0.3；`--rtt snapshot` 默认 5.0）|
| `--log` | 否 | `--rtt` 日志路径（默认 rtt.log） |
| `--tail` | 否 | `--rtt show` 只显示末尾 N 行 |
| `--device` | 否 | 芯片型号（默认 N32G4FRRE） |
| `--if` | 否 | 接口（默认 SWD） |
| `--speed` | 否 | SWD 速度 kHz（默认 4000） |
| `--backend {jlink,openocd}` | 否 | 后端：默认 jlink；ST-Link/DAP 用 openocd |
| `--ocd-if` | 否 | OpenOCD 接口配置：stlink/cmsis-dap/jlink（默认自动探测）|
| `--ocd-target` | 否 | OpenOCD target 配置（默认 `target/stm32f4x.cfg`，Cortex-M4 通用）|
| `--ocd-port` | 否 | OpenOCD TCL 端口（默认 6666）|
| `--jlink` | 否 | 显式指定 JLink.exe 路径 |
| `--dry-run` | 否 | 只打印生成的脚本/计划，不执行 |
| `--json` | 否 | 结构化 JSON 输出（供 AI 解析） |

## ⚠️ 注意事项

1. **IWDG 看门狗 —— halt 是否安全取决于固件是否冻结了它**。
   若固件里有 `DBG_ConfigPeriph(DBG_IWDG_STOP, ENABLE)`（或等价的 `DBGMCU_APB1_FZ`
   调试冻结位），内核 halt 时 IWDG 停止计数，**halt 多久都安全**。
   **先确认再决定**：
   ```bash
   grep -rn "DBG_IWDG_STOP\|DBG_ConfigPeriph\|DBGMCU" <源码目录>
   ```
   - **有冻结** → `--bp` / `--mem` 放心用；
   - **没有** → halt 期间看门狗照跑，缩短 `--run-ms`，或改走免停机路径。

   ⚠️ 不要因为「本类设备可能有看门狗」就不敢做非侵入调试 —— `savebin` 后台读与
   `--mem --no-halt` / `--rtt snapshot` **根本不 halt**，与看门狗无关，是取证的主力通道。
2. **RTT 取历史用 `snapshot`**：`--rtt start` 必须**先于复位**才抓得到启动日志；测试已经跑完
   想回读、或复现偶发问题，用 `--rtt snapshot`（见上方三方式对比表）。
3. **符号歧义**：模糊匹配可能命中多个符号（如 `Display` → 6 个），先用 `--resolve` 核对再下断点。
4. **断点上限**：J-Link 硬件断点约 6 个，设置过多会失败。
5. **「无法连接目标」先查占用**：残留的 `JLinkRTTLogger` / `JLinkGDBServerCL` / Keil 会独占
   J-Link，现象与「探针没插/芯片没供电」完全一样。工具在连接失败时会自动列出可疑进程。
6. **gdb 源码级调试**：已随 GNU Arm Toolchain 14.2 安装（`D:\ruanjian\arm-gnu-toolchain\bin`，已入用户 PATH）。未检测到时自动降级提示，`--bp/--mem/--regs` 不受影响。
7. **VFP 寄存器警告**：Cortex-M4F 的 gdb 读到 fpscr 后尝试读浮点扩展寄存器（s0-s31）时，JLink GDBServer 响应解析会报 `Expected an decimal digit`——**无害**，核心寄存器（pc/lr/sp/xpsr/r0-r12）已全部正确读出，可忽略。
8. **gdb + GDBServer 的两个坑**：
   - `-singlerun` **不能加**：它会把一次 TCP 端口探测当成唯一会话，之后真正的 gdb 连接被拒（`error 138`）。
   - **RTT telnet 19021 只允许一路连接**：用 socket 探测端口会占掉这唯一名额，后续报
     `There already is an active connection`。要连就**直连并保持，不要探测**。
9. **OpenOCD 无停机**：`--backend openocd` 的 `--mem/--regs` 用 TCL `mdw`/`reg` 运行中直读，**不 halt CPU**——与 jlink 的 `--mem --no-halt` 等效，特别适合 `--watch` 持续采样。OpenOCD 自动拉起（已开端口则复用），退出时仅停自己拉起的进程。
10. **OpenOCD 无 N32 target**：OpenOCD 0.12 无 N32 厂商 target，N32G4FR 是 Cortex-M4，用 `target/stm32f4x.cfg` 通用配置即可（`--ocd-target` 可换）。`--rtt/--bp/--gdb/--reset-run` 仅 jlink 后端支持，openocd 下会明确报错。
11. **RTT 日志编码**：Keil 工程 RTT 输出通常是 **GBK**，现代工具链多为 UTF-8。默认 `--encoding auto`
    先试 UTF-8、失败回落 GBK，两边都不误伤；显式指定可覆盖。

## 常见错误

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| 未找到 JLink.exe | 工具路径未注册 | 运行 `/ea setup` 注册，或 `--jlink` 指定路径 |
| 无法连接目标 | 调试器未接/芯片供电/**被占用** | 先看工具列出的占用进程，再查 USB/供电，关闭 Keil 调试 |
| RTT 日志有 `�` 乱码 | 编码不符（Keil 多为 GBK）| `--encoding gbk`（或保持默认 `auto`）|
| 拿不到已发生的 RTT 日志 | 用了 `--rtt start`（只能抓新数据）| 改用 `--rtt snapshot` |
| RTT 取不到数据 / 控制块校验不过 | `_SEGGER_RTT` 地址不对 | `--list-symbols _SEGGER_RTT` 核对；或 `--cb-addr` 指定 |
| RTT 采样报「疑似丢失」 | `--interval` 太长、输出太快 | 缩短 `--interval` |
| 采样打断了交互测试 | 用了默认 `--mem`（halt→读→go）| 加 `--no-halt` |
| 找不到符号 | 无 .map 或名字不符 | `--list-symbols` 查看，`--map` 指定文件 |
| halt 后复位 | IWDG 未被调试冻结 | 确认 `DBG_IWDG_STOP`；缩短 `--run-ms`；或改 `--mem --no-halt` |
| gdb 不可用 | arm-none-eabi-gdb 不在 PATH | 确认 `D:\ruanjian\arm-gnu-toolchain\bin` 在 PATH；或重开终端刷新环境 |
| gdb 连接被拒 `error 138` | GDBServer 加了 `-singlerun` | 去掉该参数 |
| RTT telnet 报已有连接 | 端口探测占用了唯一名额 | 直连不要探测 |
| 未找到 openocd | 工具未注册/不在 PATH | `/ea setup` 注册，或加入 PATH |
| OpenOCD 启动失败 | 接口不匹配/调试器被占用 | `--ocd-if stlink/cmsis-dap` 指定；关闭 Keil 调试 |
| OpenOCD 只支持 mem/regs | 用了 --rtt/--bp/--gdb/--reset-run | 换 `--backend jlink` 或改 `--mem/--regs` |

## 源文件编码注意

涉及修改工程 `.c`/`.h` 时：本类 Keil 工程源文件为 **GB2312（GBK）** 编码。读取用 `encoding="gbk"`，修改用字节级 Python 操作，禁止 UTF-8 编辑器直接改中文（会整文件乱码）。详见 `build.md` 的编码章节。

## 相关文件
- `~/.claude/skills/EA-SKILL/tools/jlink-debug/scripts/jlink_debug.py` - 调试工具脚本
- `commands/build.md` - 编译说明
- `commands/flash.md` - 烧录说明
- `commands/serial.md` - 串口监控说明
