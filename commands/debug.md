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
BJ="py <SKILL>/tools/jlink-debug/scripts/jlink_debug.py"

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
# ⚠️ 符号名**必须来自本工程**的 .map —— 先用 --list-symbols <关键字> 查（见「符号解析」）
$BJ --bp <本工程函数名> --run-ms 2000
$BJ --bp 0x08001d28                        # 也可直接给地址（Thumb bit 可带可不带）

# ⑦ 内存监视：变量值是多少？
$BJ --mem <本工程变量名>                    # 默认 4×32bit，halt→读→go
$BJ --mem <本工程变量名> --no-halt --watch 10   # 不打断正在跑的目标
$BJ --mem <本工程变量名> --watch 5
$BJ --mem 0x20020228 --count 8 --width 1

# ⑧ 读寄存器
$BJ --regs

# ⑨ 源码级调试（可选，需 arm-none-eabi-gdb）
$BJ --gdb --elf build/app.axf --gdb-script debug.gdb

# ⑩ ST-Link/DAP 后端：内存/寄存器免断点实时监控（OpenOCD，不 halt CPU）
$BJ --backend openocd --mem <本工程变量名> --watch 10 --interval 0.5
$BJ --backend openocd --regs
# --ocd-if stlink（默认）| cmsis-dap；--ocd-target 指定 target（默认 target/stm32f4x.cfg 通用 Cortex-M4）
# OpenOCD 自动拉起/复用（先查 6666 端口，已开则复用不打扰用户进程）

# 辅助：符号表 / 地址解析（AI 先核对映射再下断点）
$BJ --list-symbols <关键字>        # 如 --list-symbols Timer / --list-symbols OM
$BJ --resolve <本工程符号名>
```

## RTT 采集：三种方式的可靠性差异（重要）

需要 RTT 日志时，**先按用途选方式**——它们的能力差别很大，选错会拿到不可信的证据：

| 方式 | 能拿历史数据 | 丢行 | 适用场景 |
|------|-------------|------|----------|
| `--rtt snapshot`（savebin 直读 RAM）| ✅ **全部** | **逐字节精确** | ✅ **取证 / 回读已发生的日志**（读走即推进 RdOff）|
| `--rtt start`（JLinkRTTLogger）| ❌ 只能抓 attach 之后 | 高吞吐时会丢 | 先 start 再复位，抓启动日志 |
| `JLinkGDBServerCL -RTTTelnetPort` | ❌ 只能抓 attach 之后 | **随机丢行**（实测 RUN1 丢 `[12]`、RUN2 丢开头两行）| ⚠️ **不可用于取证** |

**要证据就用 `--rtt snapshot`**。它与 JLinkRTTLogger 的根本差别：Logger 是「订阅」——只收到订阅之后目标推来的数据；snapshot 是**直接读目标 RAM 里的 SEGGER RTT 环形缓冲**，所以已发生的日志也在里面。

原理（工具已封装，仅供理解）：

```
1. savebin 读 SEGGER_RTT_CB 控制块（acID[16] + NumUp + NumDown + aUp[0]）
2. 校验 cb[0:10] == b"SEGGER RTT"     ← 挡住 savebin 错位读出的垃圾
3. 从 aUp[0] 取 pBuffer / SizeOfBuffer / WrOff / RdOff / Flags
4. 按 WrOff 取环形缓冲 [rd, wr) 区间，处理回绕
5. 轮询模式下记住上一轮 WrOff，只取新增段
6. 把 RdOff 推进到已读位置（`w4` 后台写）—— **唯一一处会写目标 RAM**，见下节
```

**丢行判定**：轮询时若两次采样之间目标写出量接近整圈（`--interval` 太长 / 输出太快），工具会告警并计入疑似丢失字节。缩短 `--interval` 即可。

### RTT 是单消费者缓冲：不推进 RdOff 会丢日志，甚至锁死目标

环形缓冲是**单消费者**设计：读取端读走 `[RdOff, WrOff)` 后有责任推进 RdOff，把空间还给目标。
`JLinkRTTViewer` / `JLinkRTTLogger` 这类正规 RTT 主机会推进；**`savebin` 直读不会** ——
所以这一步必须由本工具自己补上。不补的后果按 `Flags`（RTT 上行缓冲模式）分两种：

| Flags | 写满时目标的行为 | 现象 |
|-------|-----------------|------|
| `NO_BLOCK_SKIP`(0) / `NO_BLOCK_TRIM`(1) | 丢弃这次写入，目标照常跑 | **WrOff 冻结**，之后所有 `SEGGER_RTT_printf` 被静默丢弃。实测 2048B 缓冲跑到第 8 轮停在 2046，看起来像"目标卡死"，其实目标在正常跑——是本工具把证据弄丢了 |
| `BLOCK_IF_FIFO_FULL`(2) | `SEGGER_RTT_Write` **自旋等 RdOff** | 没人推进 → **目标永久锁死在该自旋里**，外部现象与死机/跑飞无法区分，极易误判成软件 bug |

后者是"只读取证"最危险的一面：一个声称非侵入的工具反而把目标弄挂了。所以默认**采集后把 RdOff
推进到已读位置**（`w4` 写控制块，走 AHB-AP 后台写，同样不 halt、不复位）：

- 轮询时搭**下一轮脚本的车**（`w4` 排在 savebin 之前），零额外 JLink 启动开销；
  放前面也是为了尽早释放空间——savebin 要搬运整个环缓冲，把 `w4` 放后面就等于让缓冲多满这么久。
- 收尾再补一次独立写回。**单次快照走的正是这条路**：它读完即结束，没有"下一轮"可搭车。
- 写的值是本工具**已经读走**的那段，不会吃掉还没读的数据；写的是 32 位对齐字段，目标侧只需保证 WrOff 只增，不存在读到半截数据的可能。
- 工具会**回读校验**写回是否真的生效，不生效就明确报警，不会假装没事。

代价：读走即消费 —— 和 RTTViewer 一样，同一段历史只能被读一次（要留存就加 `--out`）。
需要退回**纯只读**（一个字节都不写目标 RAM）时加 `--no-consume`，但那两种后果随之而来，
工具会在采集开始时说明。

### 不要和 JLinkRTTViewer 同时开

**RTTViewer 开着的时候，`--rtt snapshot` 一定失败**，原因有两层：

1. **探针独占**：J-Link 一次只允许一个进程连接。RTTViewer 占着时，本工具所有需要连
   J-Link 的操作（snapshot / bp / mem / regs / reset-run / rtt start）都会报「无法连接目标」。
2. **历史被排空**（更隐蔽）：RTTViewer 是正规 RTT 主机，会持续读并**推进 `RdOff`**。
   只要它开着，环形缓冲就一直是空的——事后再跑 `--rtt snapshot` 也取不回那段数据，
   因为它已经被 RTTViewer 消费掉了。

两者是**按阶段二选一**，不是互补：

| 你想干什么 | 用什么 | 代价 |
|-----------|--------|------|
| 实时盯着看 | RTTViewer 窗口 | 事后取不回历史 |
| 拿历史 / 取证 | `--rtt snapshot`（先关掉 RTTViewer）| 没有实时视图 |
| 既要实时又要事后 | `--rtt start` 写日志 → `--rtt show` 读文件 | 探针仍被占，不能同时开 RTTViewer |
| 既要实时又要能下断点 | GDBServer 持探针 + RTTViewer 选 **"Existing Session"**（attach，不占探针）| 本工具的 `--bp/--mem/--rtt snapshot` 全部不可用 |

**本工具自己的 `--rtt start` 也会占住探针** —— 它后台起的就是 `JLinkRTTLogger.exe`。
在 `--rtt stop` 之前，凡走 `JLink.exe` 的操作（`--rtt snapshot` / `--bp` / `--mem` /
`--regs` / `--reset-run`）一律连不上：**这两个进程不能同时占用探针**，是 J-Link 的硬性
限制，不是配置问题。所以「一边 Logger 实时抓、一边 snapshot 取证」做不到，只能二选一。

> **修正**：早先这里还写着"RTTViewer / Logger 开着时**烧录也会失败**"，**实机证伪了** ——
> RTTViewer（PID 15268）与 Keil `UV4.exe` 同时开着，J-Link 原生通道仍正常连接并烧录成功
> （Commander 带 `-AutoConnect 1`，连接不受另一进程影响）。所以别再拿"烧录失败"当作
> "RTTViewer 没关"的证据往那边排查。**尚未实测**的是 snapshot/bp/mem 在 RTTViewer 开着时
> 究竟是"连不上"还是"连得上但历史已被排空" —— 后者更符合上面第 2 条，排查时先按
> "历史被排空"想，别一口咬定是连接被拒。

**工具已内置防护**，不必靠人记得关：

- `--rtt snapshot` 开跑前扫描占用进程，命中即**中止采集**并列出 PID，要求关掉后重试；
  确认要带病采集才加 `--force`（`--rtt start` 同理，但它的 `--force` 是清理残留 Logger）。
- 首轮读到空缓冲（`wr == rd`）时**不再静默返回 0 字节**，而是按**占用清单**分两种情形报：
  - 有 RTT 读取端在跑（`JLinkRTTViewer/Logger/Client`）→ 「已被其它 RTT 读取端取空」
  - 没有 → 「目标尚未通过 RTT 输出，**或**日志已被覆盖整圈」

  两者排查方向完全相反：前者要关掉 RTTViewer，后者要查固件有没有调用 `SEGGER_RTT_Init`。

  ⚠️ **后一种是两因合报，不是工具偷懒**：只看缓冲状态（`wr == rd`、`RdOff`）**无法**区分
  「目标从没写过」与「写了一整圈后被覆盖」—— 这两种情形在缓冲里留下的痕迹完全相同。
  要分开得靠旁证：固件是否链接了 SEGGER_RTT、`--rtt start` 期间能否抓到新数据、
  缓冲 size 是否小于目标一次启动的输出量。**别指望这条提示能替你把两因分开。**
- 扫描名单**排除 Keil**（`UV4.exe`）：它只在调试会话进行中才占探针，
  算进去会在你只是开着 Keil 时误伤。

### 为什么可以「随便暂停读 RAM」——JLink Commander 的非侵入性

三点前提（都经实测确认，写进这里以免被误解而不敢用）：

1. `JLink.exe -AutoConnect 1 -CommanderScript f` 里的 `connect` **既不复位也不 halt** 目标（输出 `CPU is not halted !`）。很多人以为 `connect` 会复位目标从而不敢用这条路——恰恰相反，它是唯一的取证通道。
2. `savebin` 走 **AHB-AP 后台访问**，对目标**零干扰**：目标全速运行（含中断、外设、喂狗）时也能反复读。
3. 前提是 IWDG 已被调试冻结——见下方「注意事项 1」。

工具已将这些固化：`--rtt snapshot` / `--mem --no-halt` 全程不 halt、不复位。

## 符号解析

工具自动扫描工作区 `**/*.map`（Keil Listing 产物）解析 Image Symbol Table 段（含 Global + Local Symbols，static 变量也能按名监视）。

⚠️ **符号名是工程相关的 —— 下面只是格式示意，不是可直接照抄的名字**。
每个工程的符号都不同，用之前一律先 `--list-symbols <关键字>` 取本工程的真实名字；
照抄会得到「找不到符号」。

```
<函数名>   0x08001d29   Thumb Code    size=80  ehtimer.o(...)
<变量名>   0x20020228   Data          size=4   ehtimer.o(.data)
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
| `--force` | 否 | **强行走过占用检查**。`--rtt start`：清理已占用的 JLinkRTTLogger；`--rtt snapshot`：忽略检测到的 RTTViewer/Logger/GDBServer 占用，继续采集（结果可能不完整）|
| `--no-consume` | 否 | `--rtt snapshot` **只读采集**，不把 RdOff 推进到已读位置。默认会推进——不推进时目标侧缓冲写满后会静默丢弃后续日志（`NO_BLOCK_SKIP`），`BLOCK_IF_FIFO_FULL` 模式下更会把目标锁死 |
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
| `--ocd-if` | 否 | OpenOCD 接口配置：stlink/cmsis-dap（`jlink` 在 Windows 受 SEGGER 驱动阻断，见注意事项 11）|
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
   想回读、或复现偶发问题，用 `--rtt snapshot`（见上方三方式对比表）。它读走即推进 RdOff
   （要留存加 `--out`），细节见「RTT 是单消费者缓冲」一节。
3. **符号歧义**：模糊匹配可能命中多个符号（如 `Display` → 6 个），先用 `--resolve` 核对再下断点。
4. **断点上限**：J-Link 硬件断点约 6 个，设置过多会失败。
5. **「无法连接目标」先查占用**：`JLinkRTTViewer` / `JLinkRTTLogger` / `JLinkGDBServerCL` /
   Keil 都会独占 J-Link，现象与「探针没插/芯片没供电」完全一样。工具在连接失败时会自动列出
   可疑进程。（Keil 仅在调试会话进行中占用。）
   `--rtt snapshot` 更把这一步**提到了采集之前**：开跑前就扫描并阻断，不再等失败才发现。
6. **gdb 源码级调试**：已随 GNU Arm Toolchain 14.2 安装（`D:\ruanjian\arm-gnu-toolchain\bin`，已入用户 PATH）。未检测到时自动降级提示，`--bp/--mem/--regs` 不受影响。
7. **VFP 寄存器警告**：Cortex-M4F 的 gdb 读到 fpscr 后尝试读浮点扩展寄存器（s0-s31）时，JLink GDBServer 响应解析会报 `Expected an decimal digit`——**无害**，核心寄存器（pc/lr/sp/xpsr/r0-r12）已全部正确读出，可忽略。
8. **gdb + GDBServer 的两个坑**：
   - `-singlerun` **不能加**：它会把一次 TCP 端口探测当成唯一会话，之后真正的 gdb 连接被拒（`error 138`）。
   - **RTT telnet 19021 只允许一路连接**：用 socket 探测端口会占掉这唯一名额，后续报
     `There already is an active connection`。要连就**直连并保持，不要探测**。
9. **OpenOCD 无停机**：`--backend openocd` 的 `--mem/--regs` 用 TCL `mdw`/`reg` 运行中直读，**不 halt CPU**——与 jlink 的 `--mem --no-halt` 等效，特别适合 `--watch` 持续采样。OpenOCD 自动拉起（已开端口则复用），退出时仅停自己拉起的进程。
10. **OpenOCD 无 N32 target**：OpenOCD 0.12 无 N32 厂商 target，N32G4FR 是 Cortex-M4，用 `target/stm32f4x.cfg` 通用配置即可（`--ocd-target` 可换）。`--rtt/--bp/--gdb/--reset-run` 仅 jlink 后端支持，openocd 下会明确报错。
11. **⚠️ `--ocd-if jlink` 在 Windows 上默认用不了**：装了 SEGGER 驱动时，OpenOCD 的 libusb 后端拿不到 J-Link 的 USB 句柄 ——
    ```
    Warn : Failed to open device: LIBUSB_ERROR_NOT_SUPPORTED
    Error: No J-Link device found
    ```
    这是**驱动层**的事，`transport select swd` 也救不了（与传输方式无关）。**有 J-Link 就用默认的 `--backend jlink`**，
    不要绕 OpenOCD。详见 `commands/flash.md` 的「OpenOCD 与 J-Link 驱动冲突」。
11. **RTT 日志编码**：Keil 工程 RTT 输出通常是 **GBK**，现代工具链多为 UTF-8。默认 `--encoding auto`
    先试 UTF-8、失败回落 GBK，两边都不误伤；显式指定可覆盖。

## 常见错误

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| 未找到 JLink.exe | 工具路径未注册 | 运行 `/ea setup` 注册，或 `--jlink` 指定路径 |
| 无法连接目标 | 调试器未接/芯片供电/**被占用** | 先看工具列出的占用进程，再查 USB/供电；关掉 RTTViewer 窗口 / Keil 调试 |
| RTTViewer 开着时 snapshot 失败或取不到历史 | RTTViewer 独占探针且已排空 `RdOff` | 关掉 RTTViewer 再 `--rtt snapshot`（见「不要和 JLinkRTTViewer 同时开」）|
| `--rtt start` 期间 `--rtt snapshot` / `--bp` / `--mem` 连不上 | `JLinkRTTLogger.exe` 与 `JLink.exe` **不能同时占用探针** | 先 `--rtt stop`。要取证就 `--rtt snapshot` 一条路走到底 |
| `--rtt snapshot` 开跑即中止并列出占用 PID | 采集前的占用检查命中 | 关掉列出的进程后重试；确认要带病采集加 `--force` |
| RTT 日志有 `�` 乱码 | 编码不符（Keil 多为 GBK）| `--encoding gbk`（或保持默认 `auto`）|
| 拿不到已发生的 RTT 日志 | 用了 `--rtt start`（只能抓新数据）| 改用 `--rtt snapshot` |
| 快照报「**连接失败**：JLink 未返回任何数据」| 探针被占用 / USB / 供电 —— **与地址无关** | 查占用进程，再查 USB、芯片供电、SWD 接线 |
| 快照报「**地址无效**：…不是 SEGGER_RTT_CB」| `_SEGGER_RTT` 地址不对 —— **与探针无关** | `--list-symbols _SEGGER_RTT` 核对；或 `--cb-addr` 指定 |
| 快照报「环形缓冲为空」| 有读取端在跑 → 被它取空；否则 → 目标未输出**或**日志被覆盖整圈 | 前者关掉 RTTViewer；后者查固件是否调过 `SEGGER_RTT_Init`。后一情形工具**不再细分**（两因在缓冲里痕迹相同，见「RTT 采集」）|
| 快照报「RTT 未初始化」/「控制块字段不合理」| 目标未调 `SEGGER_RTT_Init`，或地址错位 | 确认固件已初始化 RTT；核对地址 |
| RTT 采样报「疑似丢失」 | `--interval` 太长、输出太快 | 缩短 `--interval` |
| 快照报「环形缓冲已占用 N/M B（接近写满）」 | 缓冲快满，日志正在被丢 | 工具默认会推进 RdOff 解掉；若是 `--no-consume`，去掉该参数 |
| 日志到某一轮**突然不再更新**（WrOff 冻结） | 环形缓冲写满，目标按 `NO_BLOCK_SKIP` 丢弃日志 | **这不是目标卡死**，目标仍在正常跑。工具默认已推进 RdOff；确认没用 `--no-consume` |
| 目标「死机/跑飞」，复位后又能跑 | `BLOCK_IF_FIFO_FULL` 下 RdOff 无人推进 → `SEGGER_RTT_Write` 自旋锁死 | 用 `--rtt snapshot` 看 `Flags` 确证；本工具默认会推进 RdOff 解除自旋 |
| 快照报「RdOff 写回未生效」/「收尾写回失败」 | `w4` 没生效（RTT 被重新初始化 / 目标复位 / 写保护）| 目标仍在丢日志或锁死自旋。重跑一次；长期看改用 `--rtt start` 让 Logger 当正规 RTT 主机 |
| 采样打断了交互测试 | 用了默认 `--mem`（halt→读→go）| 加 `--no-halt` |
| 找不到符号 | 无 .map 或名字不符 | `--list-symbols` 查看，`--map` 指定文件 |
| halt 后复位 | IWDG 未被调试冻结 | 确认 `DBG_IWDG_STOP`；缩短 `--run-ms`；或改 `--mem --no-halt` |
| gdb 不可用 | arm-none-eabi-gdb 不在 PATH | 确认 `D:\ruanjian\arm-gnu-toolchain\bin` 在 PATH；或重开终端刷新环境 |
| gdb 连接被拒 `error 138` | GDBServer 加了 `-singlerun` | 去掉该参数 |
| RTT telnet 报已有连接 | 端口探测占用了唯一名额 | 直连不要探测 |
| 未找到 openocd | 工具未注册/不在 PATH | `/ea setup` 注册，或加入 PATH |
| OpenOCD 启动失败 | 接口不匹配/调试器被占用 | `--ocd-if stlink/cmsis-dap` 指定；关闭 Keil 调试 |
| OpenOCD 只支持 mem/regs | 用了 --rtt/--bp/--gdb/--reset-run | 换 `--backend jlink` 或改 `--mem/--regs` |
| OpenOCD 报 `LIBUSB_ERROR_NOT_SUPPORTED` + `No J-Link device found` | SEGGER 驱动独占 USB，OpenOCD 拿不到句柄（`--ocd-if jlink` 在 Windows 默认不可用）| **换默认的 `--backend jlink`**；别去调 transport |

## 源文件编码注意

涉及修改工程 `.c`/`.h` 时：本类 Keil 工程源文件为 **GB2312（GBK）** 编码。读取用 `encoding="gbk"`，修改用字节级 Python 操作，禁止 UTF-8 编辑器直接改中文（会整文件乱码）。详见 `build.md` 的编码章节。

## 相关文件
- `<SKILL>/tools/jlink-debug/scripts/jlink_debug.py` - 调试工具脚本
- `commands/build.md` - 编译说明
- `commands/flash.md` - 烧录说明
- `commands/serial.md` - 串口监控说明
