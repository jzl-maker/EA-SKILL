# 命令: /ea la (逻辑分析仪)

## 功能

Saleae 逻辑分析仪（Logic 8 / Logic 16 / Pro 8 / Pro 16）抓取 + 协议解码，用于排查 UART/SPI/I2C 等总线时序。

| 能力 | 参数 | 底层 | 依赖 |
|------|------|------|------|
| 数字/模拟抓取 | `--capture` | Logic 2 Automation API | Logic 2 软件 + `logic2-automation` |
| 边沿触发抓取 | `--trigger-channel` / `--trigger-edge` | `DigitalTriggerCaptureMode` | 同上 |
| 协议解码（看帧结构） | `--decoder` | `add_analyzer` + `export_data_table` | 同上 |
| **软件解码（取字节值）** | `--decode-uart` / `--decode-spi` / `--decode-i2c` | `waveform.py` 直接解 `digital.csv` | **仅标准库** |
| 通道素描 | `--export-raw` 自动 | `waveform.py` 统计每通道 | **仅标准库** |
| 原始数据导出 | `--export-raw` | `export_raw_data_csv`（写目录） | 同上 |
| 捕获保存/复用 | `--save` / `--load` | `save_capture` / `load_capture` | 同上 |
| 波形图 | `--plot` | numpy + matplotlib | + numpy/matplotlib |
| 无硬件自测 | `--simulate` | Logic 2 内置模拟设备 | Logic 2 软件即可 |

**与 jlink-debug 不同**：这是唯一依赖第三方 pip 包 + 后台 GUI 软件的工具。Logic 2 软件需单独安装（`winget install Saleae.Logic2` 或官网下载），且抓取前需开启「脚本服务器」。

## 调用方式

```bash
py <SKILL>/tools/instrument/scripts/logic_analyzer.py --detect
# ✅ 确认依赖 / Logic 2 软件 / 脚本端口 10430

# 无硬件自测：模拟设备抓 0.5s + I2C 解码 + 出图
py .../logic_analyzer.py --simulate --capture --duration 0.5 \
  --decoder "i2c:SCL=0,SDA=1" --export-raw --save sim.sal --plot la.png

# 真实抓取：4 通道 10M 采样 2s + UART 解码
py .../logic_analyzer.py --capture --channels 0,1,2,3 --sample-rate 10000000 \
  --duration 2 --decoder "uart:TX=0,RX=1,Bit Rate (Bits/s)=115200" --export-raw

# 复用已有 .sal 重新解码
py .../logic_analyzer.py --load trace.sal --decoder "spi:CLK=0,MOSI=1,MISO=2"

# 拿精确字节（不经 Logic 2 解码器，自动开 --export-raw）
py .../logic_analyzer.py --capture --channels 7 --duration 2 \
  --decode-uart "ch=7,baud=auto"

# SPI：按片选切帧，mode 明给（给定 mode 不靠猜）
py .../logic_analyzer.py --capture --channels 0,1,2,3 --sample-rate 10000000 \
  --duration 1 --decode-spi "clk=0,mosi=1,miso=2,cs=3,mode=0,bits=8"

# I2C：直接解出 START/地址/读写位/ACK/NACK
py .../logic_analyzer.py --capture --channels 4,5 --duration 2 \
  --decode-i2c "scl=4,sda=5"

# 抓偶发事件：只录 ch7 下降沿之后 0.2s，不必硬录满全程
py .../logic_analyzer.py --capture --channels 7,15 --sample-rate 10000000 \
  --trigger-channel 7 --trigger-edge falling --duration 0.2 --export-raw

# 纯逻辑核对（不碰设备，打印将执行的 API 序列）
py .../logic_analyzer.py --dry-run --capture --decoder "spi:CLK=0,MOSI=1,MISO=2,Enable=3"
```

## 参数表

| 参数 | 必须 | 说明 |
|------|------|------|
| `--detect` | 是（动作） | 探测依赖 / Logic 2 软件 / 端口 / 设备 |
| `--list-devices` | 是（动作） | 列出已连接设备（含模拟设备） |
| `--list-analyzer-types` | 是（动作） | 常用解码器类型速查 |
| `--capture` | 是（动作，默认） | 抓取主流程 |
| `--load <file.sal>` | 是（动作） | 加载已有捕获做解码/导出 |
| `--channels 0,1,2,3` | 否 | 数字通道（默认 0..7） |
| `--sample-rate` | 否 | 数字采样率 Hz（默认 10M；Logic 8 ≤100M，Pro 单通道 ≤500M） |
| `--duration` | 否 | 抓取时长秒（默认 1.0）；**触发模式下含义变成"触发后录多久"** |
| `--trigger-channel <N>` | 否 | 数字触发通道：该通道满足触发条件才开始录（抓偶发事件，避免硬录满全程） |
| `--trigger-edge` | 否 | 触发条件 `rising`(默认)/`falling`/`pulse-high`/`pulse-low` |
| `--trigger-timeout <S>` | 否 | **等触发的上限**秒（默认 120，`0`=无限等待）；不含触发后录制那段 |
| `--threshold` | 否 | Pro 系列阈值档位 1.2/1.8/3.3 V（Logic 8/16 忽略，用默认档位） |
| `--analog-channels` | 否 | 使能模拟通道（Logic 8 / Pro 8 支持） |
| `--decoder <TYPE:K=V,...>` | 否 | 添加 Logic 2 解码器（看帧结构），**可重复** |
| `--decode-uart <spec>` | 否 | 内置 UART 解码取精确字节，**可重复**。`ch=7,baud=57600` / `TX=7,RX=15,baud=auto` / `7,57600`；baud 给 `auto` 按最短脉宽粗估。自动开启 `--export-raw` |
| `--decode-spi <spec>` | 否 | 内置 SPI 解码，**可重复**。`clk=0,mosi=1,miso=2,cs=3`（`sck/sdi/sdo/nss` 同义）+ `mode=0..3\|auto` `bits=N` `order=msb\|lsb`。至少给 clk 和 mosi/miso 之一；**给了 cs 就按片选切帧（最可靠）**，没给则按时钟间隙切。自动开启 `--export-raw` |
| `--decode-i2c <spec>` | 否 | 内置 I2C 解码，**可重复**。`scl=3,sda=4` 或位置形式 `3,4`。输出 START/STOP、重复 START、7 位地址 + 读写位、每字节 ACK/NACK。自动开启 `--export-raw` |
| `--export-raw` | 否 | 导出原始数据 CSV（写目录） |
| `--export-dir` | 否 | 导出目录（默认 `<STATE_DIR>/captures/la_<ts>/`） |
| `--save <file.sal>` | 否 | 保存 .sal 捕获文件 |
| `--plot [file.png]` | 否 | 波形图 png（默认 la_waveform.png） |
| `--port` | 否 | Logic 2 脚本端口（默认 10430） |
| `--launch` | 否 | Logic 2 未运行时自动启动 |
| `--simulate` | 否 | 内置模拟设备（无硬件自测） |
| `--logic2-path` | 否 | 显式指定 Logic 2 可执行文件 |
| `--device` | 否 | 多设备时按名/ID 过滤 |
| `--dry-run` | 否 | 只打印 API 调用序列，不连接 |
| `--json` | 否 | 结构化 JSON 输出（供 AI 解析） |

## 工作流程（AI 执行闭环）

```
1. --detect          确认 logic2-automation / Logic 2 软件 / 端口 10430
2. 连接              未运行 → --launch 或提示用户手动开脚本服务器
3. 配置              --channels --sample-rate --threshold
4. 抓取              偶发事件 → --trigger-channel/--trigger-edge（只录触发前后）
                     常态信号 → --duration N（TimedCaptureMode）
5. 取字节（可选）    --decode-uart / --decode-spi / --decode-i2c 直接拿精确字节，
                     不经 Logic 2（UART 可 baud=auto；SPI 给 mode；I2C 只要两条通道）
6. 解码              --decoder "uart:TX=0,RX=1,Bit Rate (Bits/s)=115200"（看帧结构）
7. 导出              --export-raw（digital.csv，自动附通道素描）+ 解码表 + --save .sal
8. 分析              **分两路，别混**：帧结构 / 时序 / ACK → decoded.csv；
                     **字节值 → digital.csv**（有损，见「两条解码路径」）
                     省事就走第 5 步 --decode-uart
9. 结论              通道对应关系用第 7 步的通道素描佐证，别只凭人工数跳变
```

示例闭环：抓 I2C → `--decoder "i2c:SCL=0,SDA=1"` → 解码表里看**地址与 ACK 的位置时序**（不采信数据列的字节值）→ 判断从机是否响应。

## 两条解码路径（务必分清）

| | `--decoder` → decoded.csv | `--decode-uart/spi/i2c` → 精确字节 |
|---|---|---|
| 数据源 | Logic 2 的 analyzer 输出 | `digital.csv` 的跳变时刻 |
| 适合 | 帧结构、时序、ACK/错误标志 | **字节值** |
| 二进制协议 | ⚠️ **有损，不可信** | ✅ 精确 |
| 依赖 | Logic 2 analyzer 定义 | 仅标准库 |
| 支持的协议 | Logic 2 全部 analyzer | UART / SPI / I2C |

**decoded.csv 为什么不可信**：Logic 2 把**不可打印字节渲染成 `.`（0x2E）**、**NUL 渲染成字面量 `\0`**。所以 UART/SPI/I2C 这类二进制协议的数据列里，`.` 极可能不是真的 0x2E。

> 真机踩过：解码表里的 `EF 2E FF FF FF FF 2E 00` 看着像真实数据，实际 `0x01`/`0x08`/`0x82` 全被渲染成了 `.`，据此得出过完全错误的结论，浪费了一轮分析。

工具已内置防线：导出解码表后**自动扫描**，某列 `.` 占比超过 10% 时当场告警并指出退路。但告警只是提醒——**要字节值就老老实实走内置解码或自己解 `digital.csv`**。

### SPI / I2C 内置解码说明

- **SPI 采样边沿由 (CPOL, CPHA) 决定**：`采样电平 = 1 if CPOL == CPHA else 0`。数据取"采样边沿之前那一瞬"，数据在另一个边沿翻转，所以四种 mode 都成立。
- **`mode` 不给则只自检 CPOL（CPHA 按 0）**，报告里会打印判定依据。**CPHA 猜错不总能被发现**：若数据在前导沿之前就稳定（典型 CPHA=0 波形），CPHA=0 和 1 读出的值完全相同。所以**已知 mode 就显式给**。
- **SPI 切帧优先用 CS**：给了 `cs=` 就按片选低电平窗口切，最可靠；没给则按时钟间隙（相邻采样沿间隔超过约 3 个时钟周期）切，连续流容易切错。**多字器件（如 16 bit ADC）记得给 `bits=16`**，否则会被按 8 位拆开。
- **I2C 无参数可调**：SCL 高时 SDA 下降 = START、上升 = STOP；数据在 SCL 上升沿采样，第 9 位 ACK（低）/NACK（高）。报告会展开重复 START 之后的第二次传输，并把地址字节拆成 `7 位地址 + R/W`。
- **I2C 接反有专门提示**：SDA 与 SCL 标反时会解出一堆 START/STOP 但**一个字节都解不出**，报告直接点明疑似标反/通道选错——这是"看着有输出、其实全不对"的典型，别把它当成有效结果。

## 注意事项

1. **`--export-raw` 产出 `digital.csv`**（单文件：第一列 `Time [s]`，其余每列一个通道，数字通道只在电平跳变处记录），`--plot` 据此出 step 波形图。
2. **内置解码全是纯软件解码**（同一个 `waveform.py`），不经 Logic 2 analyzer，前提是先有 `digital.csv`（脚本已自动开启 `--export-raw`）。通道号写错会明确报错并列出可用通道，不会静默跳过。
   UART 的起始位用三条判据卡死（前有 ≥0.5 bit 空闲高、起始位中心仍为低、每帧整帧跳到帧尾之后），避免在数据位中间重新同步而产出成片 framing error；SPI/I2C 的判定细节见上一节。
3. **错误帧占比 ≥20% 时会明确告诉你"整份结果不可信"**。波特率给错的典型表现不是全错，而是**零星几个看着像真的伪字节 + 大量 framing error**——别直接采信，先核对 baud 和通道。
4. **导出路径由 Logic 2 后端进程解析**：相对路径会落到 Logic 2 安装目录，脚本内部已转绝对路径，`--save`/`--export-dir` 请给完整或相对路径均可（脚本统一 `.resolve()`）。
5. **analyzer 类型名/设置键必须匹配已装 Logic 2 的 Analyzer 定义**。常用类型：`Async Serial`（别名 `uart`，TX/RX 自动拆成两个 analyzer，键 `Input Channel`/`Bit Rate (Bits/s)`）、`SPI`、`I2C`、`I2S / PCM`、`CAN`、`1-Wire`、`Modbus`、`SMBus`。先 `--list-analyzer-types` 或 `--dry-run` 核对；"Bit Rate (Bits/s)" 这类带空格/括号的键要完整。
6. **阈值**：Logic 8/16 后端不接受自定义阈值（传了报 invalid voltage setting），脚本自动忽略；Pro 8/16 支持 1.2/1.8/3.3 V。
7. **采样率上限**：Logic 8/16 聚合 ≤100M，Pro 8/16 单通道 ≤500M，超出报错。
8. **MDIO/CAN/LIN 原生导出被 Logic 2 禁用** → 统一走 `export_data_table` 取解码表。
9. **多次抓取前 `capture.close()`**（脚本内部已处理）。
10. **无硬件**：`--simulate` 用模拟设备跑全管线（自动选模拟设备）；`--dry-run` 纯逻辑核对。
11. **触发模式的两个坑**（`--trigger-channel`）：
   - `DigitalTriggerCaptureMode` **没有 `duration_seconds`**，触发后录制长度由 `after_trigger_seconds` 决定（脚本统一取 `--duration` 的值）。
   - Logic 2 的 `Capture.wait()` **没有超时参数**，触发不来会永久挂住。脚本用守护线程按 `--trigger-timeout + duration + 5s` 兜底中止（超时返回码 1、调 `capture.stop()`）。要原生无限等待就显式给 `--trigger-timeout 0`。
   - 注：超时中止时会并发调用 `stop()` 与 `wait()`，官方文档建议二者不要用于同一次抓取，这是为"可中止"做的取舍。
12. **长录制提醒**：无触发模式下 `--duration ≥ 30s` 会提前打印采样量与"改用触发"的建议——偶发事件硬录很容易白录（录满全程、事件一次没来）。
13. **`--export-raw` 后自动打印通道素描**（跳变数 / 按时长加权的高位占比 / 最短脉宽 / 波特率粗估）。判断"ch7 是 MCU TX 还是 RX"先看这个，比人工数跳变可靠；`--decode-uart` 给出 TX/RX 对时，若被标成 RX 的通道**先开口**会提示疑似标反（协议里主动方先发）。
14. **导出后打印产物行数与体积**，抓完立刻知道这次录了多少。
15. **`--json` 的 stdout 里只有那一份 JSON**：设备信息（`▶`）、进度（`⏳`）、产物清单（`✅`）全部改道 **stderr**，`json.loads(stdout)` 直接可用。老实现把两者都写进 stdout——`do_capture`/`do_load` 在 `emit_json` **之前**就已经 print 了设备与产物信息，调用方拿到的是"一堆中文 + 末尾一段 JSON"，解析必然失败。机制在 `common.py` 的 `diagnostics_to_stderr`，与 `scope.py` 共用；`--dry-run` 不参与改道（那条路径本来就不发 JSON）。

## 常见错误

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| No module named 'saleae' | 没装 logic2-automation | `py <SKILL>/tools/instrument/scripts/deps_check.py --install` |
| gRPC ... failed to connect | Logic 2 没跑或脚本端口没开 | 打开 Logic 2 → 设置 → 开启脚本服务器；或 `--launch` |
| 未发现设备 | 硬件未接入 | 接 USB 后 `--list-devices`；自测用 `--simulate` |
| 采样率超出上限 | Pro 设备多通道聚合超限 | 降低 `--sample-rate` 或减少通道 |
| 解码器类型名不对 | analyzer 名/键不匹配 Logic 2 版本 | `--list-analyzer-types` + `--dry-run` 核对 |
| invalid voltage setting | Logic 8/16 传了自定义阈值 | 不传 `--threshold`（脚本已自动忽略） |
| Export failed: ios_base::failbit | 导出目录不存在 | 脚本已自动 mkdir |
| --simulate 仍需 Logic 2 | 模拟器也是 Logic 2 软件功能 | 先安装 Logic 2（`winget install Saleae.Logic2`） |
| 解码表里全是 `.` | Logic 2 把不可打印字节渲染成了 0x2E | 别采信，用 `--decode-uart` 或自解 `digital.csv` |
| 解码出一堆 framing error | baud 不对 / 该通道不是 UART | 先 `baud=auto` 粗估，或看通道素描的最短脉宽 |
| 触发一直不返回 | 触发条件不对，`wait()` 无超时 | 等 `--trigger-timeout` 兜底中止后核对边沿方向与通道号 |
| 内置解码报"整体不可信" | 错误帧占比 ≥20% | 核对 baud；确认该通道真是 UART 且不是毛刺信号 |
| SPI 有 ragged 警告 | 采样沿凑不满一个字 | mode 猜错了（显式给 `mode=`）；或 CS/时钟接线不对、时钟有毛刺 |
| SPI 字节流被拆错位 | 字长不是 8 bit | 加 `bits=16`（或器件实际字长）；LSB 器件加 `order=lsb` |
| SPI 连续流切帧切错 | 无 CS 时只能按时钟间隙猜 | 把片选接上并给 `cs=`；或缩短抓取窗口只覆盖一帧 |
| I2C 已识 START 却解不出字节 | SDA/SCL 标反或通道选错 | 报告会直接提示；核对两条通道号，时钟那条跳变多、占空比接近 50% |

## 相关文件
- `<SKILL>/tools/instrument/scripts/logic_analyzer.py` - 工具脚本
- `<SKILL>/tools/instrument/scripts/waveform.py` - digital.csv 解析 / 通道素描 / 内置 UART + SPI + I2C 解码（仅标准库）
- `<SKILL>/tools/instrument/scripts/deps_check.py` - 依赖探测
- `<SKILL>/tools/instrument/requirements.txt` - 依赖清单
- `commands/scope.md` - 示波器命令（同类仪器）
