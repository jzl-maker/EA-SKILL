# 工作流: verify-flow（嵌入式验证四连）

> 由 `commands/verify.md` 加载。EA-SKILL 纯嵌入式，此流程即默认验证流程。

## 流程总览

```
通用 verify 流程
   │
   ├─ 四连执行：
   │   ├─ ① build-keil       （编译固件）
   │   ├─ ② flash            （烧录，含校验）—— 后端可插拔：openocd / jlink-native
   │   ├─ ③ jlink-debug      （J-Link 运行验证）
   │   └─ ④ serial-monitor   （抓取业务启动日志）—— 无串口时降级为 RTT
   │
   └─ 用户口述物理现象观察 → HVR 文件
```

**关键**：AI 连续自动执行四步，用户只需观察物理现象并口述结果。

**降级路径**：四连里每一环都可能因**环境**（而非代码）不可达。不可达时**先试降级、
并把降级事实显著标注进 HVR**，不要直接判验证失败 —— 那样报的是"环境没配好"，
却看起来像"代码有问题"，是最误导人的一种失败。

| 环节 | 不可达的判据 | 降级 |
|------|-------------|------|
| ② 烧录 | `LIBUSB_ERROR_NOT_SUPPORTED` + `No J-Link device found` | 换 `--backend jlink-native`（见下） |
| ④ 串口 | 无可用 COM 口 / 目标没接 UART | **用 RTT 日志代替**，HVR 标注「串口缺失，以 RTT 代偿」 |

## ① 编译（build-keil）

```bash
python ~/.claude/skills/EA-SKILL/tools/build-keil/scripts/keil_builder.py \
  --project <工程文件路径> \
  --target <目标名> \
  --log <STATE_DIR>/logs/build_S<N>.log
```

- `--project`：扫描工作区 `*.uvprojx`/`*.uvproj`
- `--log`：**显式传**，否则日志落工程目录污染仓库（见 `commands/build.md`）
- UV4 路径：从 `tool_config`（`/ea setup` 注册）读取

结果提取（写 HVR）：编译状态 / 错误·警告数 / 固件大小 / 产物路径。
**成功 → 自动进烧录；失败 → 分析日志请求修复。**

## ② 烧录（后端可插拔）

**按探针选后端**：ST-Link / CMSIS-DAP / DAPLink → openocd；**J-Link → jlink-native**
（Windows 上装了 SEGGER 驱动时，OpenOCD 用不了 J-Link，见 `commands/flash.md`）。

```bash
# 后端 A：OpenOCD（默认）
python ~/.claude/skills/EA-SKILL/tools/flash-openocd/scripts/openocd_flasher.py --detect
python ~/.claude/skills/EA-SKILL/tools/flash-openocd/scripts/openocd_flasher.py \
  --artifact <产物路径> \
  --interface <stlink|cmsis-dap> \
  --target <target/xxx.cfg>

# 后端 B：J-Link 原生（自带独立回读校验；.bin 需 --base-address）
python ~/.claude/skills/EA-SKILL/tools/flash-openocd/scripts/openocd_flasher.py \
  --backend jlink-native \
  --artifact <产物路径> \
  --device <芯片型号>
```

- 校验状态：verified / skipped
- 失败分类：connection-failure / target-response-abnormal / project-config-error

**后端 B 的结论可直接采信**：它把 Flash 回读回来，与**从产物文件自己解析出的**期望字节
逐段比 MD5 —— 与 J-Link 自身的校验实现无关，因此能分辨 J-Link 的 `Verification failed`
是真失败还是误报。**成功 → 自动进 J-Link 运行验证。**

## ③ J-Link 运行验证（jlink-debug）

烧录后**用 J-Link 确认固件真的在跑**（不依赖串口是否已配好）：

```bash
# 读寄存器：确认 PC 进入用户区（main），SP 合法
py ~/.claude/skills/EA-SKILL/tools/jlink-debug/scripts/jlink_debug.py --regs

# 抓 RTT 启动日志（先 start 再复位，避免错过启动消息）
py ~/.claude/skills/EA-SKILL/tools/jlink-debug/scripts/jlink_debug.py --rtt start --log .ea/logs/rtt.log
# → 触发复位 → 
py ~/.claude/skills/EA-SKILL/tools/jlink-debug/scripts/jlink_debug.py --rtt stop --log .ea/logs/rtt.log
py ~/.claude/skills/EA-SKILL/tools/jlink-debug/scripts/jlink_debug.py --rtt show --log .ea/logs/rtt.log --tail 50

# 必要时读固件区内存比对
py ~/.claude/skills/EA-SKILL/tools/jlink-debug/scripts/jlink_debug.py --mem 0x08000000 --count 8
```

判定：
- PC 落在 Flash 用户区（如 `0x0800xxxx`）且 SP 合法 → 固件已启动 ✅
- RTT 抓到启动日志 → 启动路径确认 ✅
- PC 停在 0x1FFFxxxx 系统区 / 看门狗复位循环 → 启动异常，进入 `/ea debug` 定位

> ⚠️ IWDG 看门狗：halt 期间不喂狗会复位，读寄存器/内存要快；RTT start 必须先于复位。

**成功 → 自动进串口。**

## ④ 串口（serial-monitor）—— 无串口时降级为 RTT

```bash
python ~/.claude/skills/EA-SKILL/tools/serial-monitor/scripts/serial_monitor.py \
  --port COM5 --baud 115200 --duration 15 \
  --wait-reset --auto-reset \
  --interface <同上> \
  --openocd-config interface/stlink.cfg \
  --openocd-target target/stm32f4x.cfg \
  --save .ea/logs/serial_S<N>.log
```

1. 打开串口监听 → 2. OpenOCD `reset halt` 复位 → 3. MCU 重启输出完整日志 → 4. 保存到 `logs/`

常见错误：不传 `--interface` → Unsupported transport；不传 `--openocd-config` → invalid command name。

### 降级：目标没接 UART / 没有可用 COM 口

**不要报"验证失败"**。串口只是取日志的一条通道，RTT 能顶上的时候四连照样成立：

```bash
# 用 RTT 代偿串口日志（含复位前的历史，见 commands/debug.md）
py ~/.claude/skills/EA-SKILL/tools/jlink-debug/scripts/jlink_debug.py \
  --rtt snapshot --reset-run --out .ea/logs/rtt_S<N>.log
```

HVR 的「执行记录」里**必须显式标注**：`串口 | 串口缺失，以 RTT 代偿`。

⚠️ 代偿有边界，别当成等价物：

- RTT 要求固件**链接了 SEGGER_RTT** 并调用过 `SEGGER_RTT_Init`；串口日志则只需 UART 初始化。
  固件只做了 UART 输出时，RTT 取不到任何东西 —— 那是**固件没接 RTT**，不是"没有日志"。
- 串口能验的是**引脚/电平/波特率这条物理链路**，RTT 验不到。降级后「UART 配置是否正确」
  这一项在本次验证中**未被覆盖**，HVR 里要记为待补，不能因为日志拿到了就当验过了。

## HVR 字段（嵌入式执行记录）

verify 的 HVR 文件追加：

```markdown
## 嵌入式执行记录
| 步骤 | 工具 | 结果 |
|------|------|------|
| 编译 | build-keil | <成功/失败> + 产物路径 |
| 烧录 | flash（openocd / jlink-native） | <成功/失败> + backend + interface + verified |
| J-Link 验证 | jlink-debug | <PC 地址 / RTT 日志 / 异常现象> |
| 串口 | serial-monitor | <抓到启动日志/未抓到/串口缺失，以 RTT 代偿> |

> 降级过的环节要写清「原本该验什么、这次没验到什么」，别只写一个"成功"。
> 例：`串口 | 串口缺失，以 RTT 代偿（UART 物理链路本次未覆盖）`

### 物理现象（用户口述）
- <用户观察>
```

## 相关文件
- `commands/verify.md` — verify 入口（含保护区审计）
- `commands/build.md` / `flash.md` / `serial.md` / `debug.md` — 四连工具细则
- `templates/hvr-template.md` — HVR 模板
