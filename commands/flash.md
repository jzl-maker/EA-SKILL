# 命令: /ea flash (烧录)

## 功能
烧录固件到 MCU。**两条通道**：

| 通道 | 工具 | 适用 |
|------|------|------|
| `--backend openocd`（默认） | OpenOCD | ST-Link / CMSIS-DAP / DAPLink |
| `--backend jlink-native` | JLink.exe | **J-Link 探针**——Windows 上装了 SEGGER 驱动时，OpenOCD 用不了 J-Link（见下） |

## 先探测环境

```bash
# OpenOCD 通道
python <SKILL>/tools/flash-openocd/scripts/openocd_flasher.py --detect

# J-Link 通道（确认 JLink.exe 在位）
python <SKILL>/tools/jlink-debug/scripts/jlink_debug.py --detect
```

## 调用方式

**OpenOCD 通道**（ST-Link / CMSIS-DAP / DAPLink）：

```bash
python <SKILL>/tools/flash-openocd/scripts/openocd_flasher.py \
  --artifact <产物路径> \
  --interface stlink \
  --target target/stm32f4x.cfg
```

**J-Link 原生通道**（`--backend jlink-native`，烧录后**独立回读比对**）：

```bash
python <SKILL>/tools/flash-openocd/scripts/openocd_flasher.py \
  --backend jlink-native \
  --artifact build/app.hex \
  --device N32G4FRRE

# 只回读校验（不烧），或看将要执行什么
python <SKILL>/tools/flash-openocd/scripts/openocd_flasher.py \
  --backend jlink-native --artifact build/app.hex --verify-only
```

`.bin` 必须给 `--base-address 0x08000000`（BIN 自身不含地址信息，工具**拒绝猜**）。
`.hex` / `.elf` / `.axf` 自带地址，无需。加 `--json` 输出机读结论。

## 参数来源

| 参数 | 来源 |
|------|------|
| `--artifact` | 从 build-keil 的产物路径获取（AXF/ELF/HEX） |
| `--interface` | 从 `--detect` 探测结果获得（stlink/jlink/cmsis-dap） |
| `--target` | 根据芯片型号选择（GD32F407 → `target/stm32f4x.cfg`） |
| `--backend` | 探针是 J-Link → `jlink-native`；其余 → `openocd` |
| `--device` | jlink-native 专用，芯片型号（默认 `N32G4FRRE`） |
| `--settle` | jlink-native 专用，编程后静置秒数（默认 10）。**别调 0**，除非你确定板子不需要（见「编程后不能立刻回读」） |

## 结果处理

| 字段 | 检查要点 |
|------|----------|
| 烧录状态 | success / failure |
| 校验状态 | verified / skipped |
| 失败分类 | connection-failure / target-response-abnormal / project-config-error |

## 自动决策

- 烧录成功 → 提示下一步（串口观察启动日志）
- 烧录失败 → 根据失败分类引导排查

## 常见错误

| 错误类型 | 原因 | 解决方案 |
|----------|------|----------|
| connection-failure | 调试器未连接或驱动问题 | 检查调试器连接、USB驱动 |
| target-response-abnormal | 芯片未进入调试模式 | 检查芯片供电、复位电路 |
| project-config-error | target 配置文件与芯片不匹配 | 检查 --target 参数 |
| Unsupported transport | --interface 参数错误 | 使用正确的接口类型 (stlink/jlink/cmsis-dap) |
| `LIBUSB_ERROR_NOT_SUPPORTED` + `No J-Link device found` | **SEGGER 驱动占着 USB**，OpenOCD 的 libusb 后端拿不到句柄 | **换 `--backend jlink-native`**。不要试 `transport select swd` —— 见下 |
| `Verification failed @ 0x08000000`（J-Link）| 多为**误报**（回读与产物逐字节一致） | 用 `--backend jlink-native`，工具会自己给结论，不要手工比 MD5 |

## ⚠️ OpenOCD 与 J-Link 驱动冲突（硬约束）

**Windows 上装了 SEGGER 驱动时，OpenOCD 无法使用 J-Link 探针。**

```
Warn : DEPRECATED: auto-selecting transport "jtag".
Warn : Failed to open device: LIBUSB_ERROR_NOT_SUPPORTED
Error: No J-Link device found
```

**判据**：输出里同时出现 `LIBUSB_ERROR_NOT_SUPPORTED` 与 `No J-Link device found`。

**别往 transport 上查**：`--config` 传 `transport select swd` 也不解决 —— 根因是 SEGGER
驱动独占了 USB 设备，OpenOCD 的 libusb 后端**根本拿不到句柄**，与传输方式无关。
`--interface jlink` 列在可选值里只是说"如果驱动环境允许"，Windows 上默认不允许。

**替代路径**（二选一）：

1. **首选**：`--backend jlink-native`（JLink 官方通道，且自动回读校验）
2. 把 J-Link 的 USB 驱动换成 WinUSB/libusb（Zadig），让 OpenOCD 能接管 —— 换完后
   SEGGER 自家工具会失效，**通常不值得**，仅当必须用 OpenOCD 时考虑

## 注意事项

⚠️ **--interface 必须与烧录时使用的接口一致**

⚠️ **--target 必须匹配实际芯片型号**

⚠️ **J-Link 的自校验结论不要直接采信**

实测出现过 J-Link 报 `Verification failed @ 0x08000000`，但**回读与产物逐字节一致** ——
固件其实烧对了。让被怀疑的一方给自己作证，本来就不可靠。

**现在这条已经工具化了**：`--backend jlink-native` 会自己做独立回读（`savebin`）并与
**从产物文件解析出的**期望字节逐段比对 MD5，然后直接给结论：

- 「回读与产物完全一致」→ 烧录成功。J-Link 那边报什么失败都是**误报**，忽略。
- 「真失败」→ 附差异地址与期望/实际字节，再按上面「常见错误」排查。

**不要再手工比 MD5**，也不要因为 J-Link 报错就去查接线 / 供电 / 复位电路 —— 那是往错的
方向排查，会白费一整轮。

⚠️ **编程后不能立刻回读**（工具已内置，手工复核时要知道）

实测（N32G4FR + J-Link V7.94b）编程后**立刻**回读，拿到的镜像会依次是：

```
错位镜像 → 陈旧值 → 陈旧值（两次一模一样）→ 正确 → 正确   ← 约 4s / 7s / 10s / 13s
```

两点结论：① 这是**时间**在收敛，不是"多读几次"，所以工具默认 `--settle 10` 先静置
（`--settle 0` 可关）；② 第 2、3 次读**完全相同却都是陈旧的**，所以"连续两次一致就采信"
是**不可靠**的，工具要求**连续 3 次一致**才下结论。收敛不了就判「**无法定论**」
（退出码 2），而不是"真失败" —— 把可能成功的烧录误报成失败，比不给结论更坏。

> 现象只在**编程之后**出现：单纯 `r`+`g` 不会触发，`--verify-only`（不烧只读）读到的一直是对的。
> 另外**编程与回读必须分两次会话**：`-ExitOnError 1` 会在 J-Link 自校验报错处直接中止脚本，
> 同会话里排在 `loadfile` 后面的 `savebin` 根本不会执行（实测产出文件都不存在）。

⚠️ **烧录前清掉其它 J-Link 进程**

探针一次只允许一个进程连接。`JLinkRTTLogger.exe`（`/ea debug --rtt start` 起的）还开着时，
烧录会连不上或行为异常 —— 采集完 RTT 记得先 `--rtt stop`。

> **`JLinkRTTViewer.exe` 开着不会阻止烧录**（实测：RTTViewer 与 Keil `UV4.exe` 同时开着，
> J-Link 原生通道照常连接并编程成功）。它的影响在 **RTT 取证**那一侧：它会独占读取端、
> 持续推进 `RdOff` 把环形缓冲排空，导致 `--rtt snapshot` 取不到那段时间的历史。详见
> `commands/debug.md` 的「不要和 JLinkRTTViewer 同时开」。

## 相关文件
- `<SKILL>/tools/flash-openocd/scripts/openocd_flasher.py` — 烧录入口（两条通道）
- `<SKILL>/tools/flash-jlink/scripts/jlink_flasher.py` — J-Link 原生后端（回读校验）
- `commands/build.md` — 编译说明
- `commands/serial.md` — 串口监控说明
