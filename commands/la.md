# 命令: /ea la (逻辑分析仪)

## 功能

Saleae 逻辑分析仪（Logic 8 / Logic 16 / Pro 8 / Pro 16）抓取 + 协议解码，用于排查 UART/SPI/I2C 等总线时序。

| 能力 | 参数 | 底层 | 依赖 |
|------|------|------|------|
| 数字/模拟抓取 | `--capture` | Logic 2 Automation API | Logic 2 软件 + `logic2-automation` |
| 协议解码 | `--decoder` | `add_analyzer` + `export_data_table` | 同上 |
| 原始数据导出 | `--export-raw` | `export_raw_data_csv`（写目录） | 同上 |
| 捕获保存/复用 | `--save` / `--load` | `save_capture` / `load_capture` | 同上 |
| 波形图 | `--plot` | numpy + matplotlib | + numpy/matplotlib |
| 无硬件自测 | `--simulate` | Logic 2 内置模拟设备 | Logic 2 软件即可 |

**与 jlink-debug 不同**：这是唯一依赖第三方 pip 包 + 后台 GUI 软件的工具。Logic 2 软件需单独安装（`winget install Saleae.Logic2` 或官网下载），且抓取前需开启「脚本服务器」。

## 调用方式

```bash
py ~/.claude/skills/ea-skill/tools/instrument/scripts/logic_analyzer.py --detect
# ✅ 确认依赖 / Logic 2 软件 / 脚本端口 10430

# 无硬件自测：模拟设备抓 0.5s + I2C 解码 + 出图
py .../logic_analyzer.py --simulate --capture --duration 0.5 \
  --decoder "i2c:SCL=0,SDA=1" --export-raw --save sim.sal --plot la.png

# 真实抓取：4 通道 10M 采样 2s + UART 解码
py .../logic_analyzer.py --capture --channels 0,1,2,3 --sample-rate 10000000 \
  --duration 2 --decoder "uart:TX=0,RX=1,Bit Rate (Bits/s)=115200" --export-raw

# 复用已有 .sal 重新解码
py .../logic_analyzer.py --load trace.sal --decoder "spi:CLK=0,MOSI=1,MISO=2"

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
| `--duration` | 否 | 抓取时长秒（默认 1.0，TimedCaptureMode） |
| `--threshold` | 否 | Pro 系列阈值档位 1.2/1.8/3.3 V（Logic 8/16 忽略，用默认档位） |
| `--analog-channels` | 否 | 使能模拟通道（Logic 8 / Pro 8 支持） |
| `--decoder <TYPE:K=V,...>` | 否 | 添加解码器，**可重复** |
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
4. 抓取              --duration N → TimedCaptureMode
5. 解码              --decoder "uart:TX=0,RX=1,Bit Rate (Bits/s)=115200"（可多个）
6. 导出              --export-raw（digital.csv）+ 解码表 + --save .sal
7. 分析              查原始跳变 / 解码表 → 判断协议时序是否正常 → 结论
```

示例闭环：抓 I2C → `--decoder "i2c:SCL=0,SDA=1"` → 解码表里看地址字节与 ACK → 判断从机是否响应。

## 注意事项

1. **`--export-raw` 产出 `digital.csv`**（单文件：第一列 `Time [s]`，其余每列一个通道，数字通道只在电平跳变处记录），`--plot` 据此出 step 波形图。
2. **导出路径由 Logic 2 后端进程解析**：相对路径会落到 Logic 2 安装目录，脚本内部已转绝对路径，`--save`/`--export-dir` 请给完整或相对路径均可（脚本统一 `.resolve()`）。
3. **analyzer 类型名/设置键必须匹配已装 Logic 2 的 Analyzer 定义**。常用类型：`Async Serial`（别名 `uart`，TX/RX 自动拆成两个 analyzer，键 `Input Channel`/`Bit Rate (Bits/s)`）、`SPI`、`I2C`、`I2S / PCM`、`CAN`、`1-Wire`、`Modbus`、`SMBus`。先 `--list-analyzer-types` 或 `--dry-run` 核对；"Bit Rate (Bits/s)" 这类带空格/括号的键要完整。
4. **阈值**：Logic 8/16 后端不接受自定义阈值（传了报 invalid voltage setting），脚本自动忽略；Pro 8/16 支持 1.2/1.8/3.3 V。
5. **采样率上限**：Logic 8/16 聚合 ≤100M，Pro 8/16 单通道 ≤500M，超出报错。
6. **MDIO/CAN/LIN 原生导出被 Logic 2 禁用** → 统一走 `export_data_table` 取解码表。
7. **多次抓取前 `capture.close()`**（脚本内部已处理）。
8. **无硬件**：`--simulate` 用模拟设备跑全管线（自动选模拟设备）；`--dry-run` 纯逻辑核对。

## 常见错误

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| No module named 'saleae' | 没装 logic2-automation | `py ~/.claude/skills/ea-skill/tools/instrument/scripts/deps_check.py --install` |
| gRPC ... failed to connect | Logic 2 没跑或脚本端口没开 | 打开 Logic 2 → 设置 → 开启脚本服务器；或 `--launch` |
| 未发现设备 | 硬件未接入 | 接 USB 后 `--list-devices`；自测用 `--simulate` |
| 采样率超出上限 | Pro 设备多通道聚合超限 | 降低 `--sample-rate` 或减少通道 |
| 解码器类型名不对 | analyzer 名/键不匹配 Logic 2 版本 | `--list-analyzer-types` + `--dry-run` 核对 |
| invalid voltage setting | Logic 8/16 传了自定义阈值 | 不传 `--threshold`（脚本已自动忽略） |
| Export failed: ios_base::failbit | 导出目录不存在 | 脚本已自动 mkdir |
| --simulate 仍需 Logic 2 | 模拟器也是 Logic 2 软件功能 | 先安装 Logic 2（`winget install Saleae.Logic2`） |

## 相关文件
- `~/.claude/skills/ea-skill/tools/instrument/scripts/logic_analyzer.py` - 工具脚本
- `~/.claude/skills/ea-skill/tools/instrument/scripts/deps_check.py` - 依赖探测
- `~/.claude/skills/ea-skill/tools/instrument/requirements.txt` - 依赖清单
- `commands/scope.md` - 示波器命令（同类仪器）
