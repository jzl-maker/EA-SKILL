# 命令: /ea serial (串口监控)

## 功能
串口数据监控和日志抓取

EA-SKILL 内置两个串口工具：

| 工具 | 路径 | 用途 |
|------|------|------|
| serial-mcp (GUI) | `<SKILL>/tools/serial-mcp/` | 可视化串口监控，人工观察 |
| serial-monitor (CLI) | `<SKILL>/tools/serial-monitor/scripts/serial_monitor.py` | AI 自动抓取日志 |

serial-mcp 用于人工观察，serial-monitor 用于 AI 自动抓取日志。

## 串口监控 (CLI)

### 调用方式

```bash
python <SKILL>/tools/serial-monitor/scripts/serial_monitor.py \
  --port <COM端口> \
  --baud <波特率>
```

### 完整参数示例（避免错过启动消息）

**关键：必须指定 OpenOCD 参数，否则复位会失败！**

```bash
python <SKILL>/tools/serial-monitor/scripts/serial_monitor.py \
  --port COM5 \
  --baud 115200 \
  --duration 15 \
  --wait-reset \
  --auto-reset \
  --interface stlink \
  --openocd-config interface/stlink.cfg \
  --openocd-target target/stm32f4x.cfg \
  --save .ea/logs/serial_S9.log
```

### 参数说明

| 参数 | 必须 | 说明 |
|------|------|------|
| --port | ✅ | 串口端口 (如 COM6) |
| --baud | ✅ | 波特率 (如 921600) |
| --duration | 否 | 监控时长（秒） |
| --auto-reset | 否 | 自动复位 MCU |
| --interface | ✅（复位时） | 烧录接口类型 (stlink/jlink/cmsis-dap) |
| --openocd-config | ✅（复位时） | OpenOCD 接口配置文件 |
| --openocd-target | ✅（复位时） | OpenOCD 目标芯片配置文件 |
| --save | 否 | 日志保存路径 |

## 串口 GUI (MCP)

```bash
python <SKILL>/tools/serial-mcp/serial_monitor.py
```

### MCP 工具

| 工具 | 说明 |
|------|------|
| serial_read | 读取串口日志 |
| serial_status | 获取连接状态 |
| serial_send | 发送命令 |
| serial_log_file | 获取日志文件 |

## 工作流程

1. 打开串口，开始监听
2. 如果使用 `--auto-reset`，OpenOCD 执行 `reset halt` 复位开发板
3. MCU 重启后输出完整启动日志
4. 捕获完整的启动日志并保存

## 注意事项

⚠️ **--interface 必须与烧录时使用的接口一致**

⚠️ **--openocd-config 和 --openocd-target 路径必须正确**

⚠️ **避免错过启动消息：先打开串口，再执行复位**

## 常见错误

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| Unsupported transport | 未传 `--interface`，OpenOCD 自动选择错误接口 | 添加正确的 `--interface` 参数 |
| invalid command name | `--openocd-config` 路径不对 | 检查 OpenOCD 配置文件路径 |
| 串口被占用 | 其他程序占用该端口 | 关闭占用串口的程序 |
| 连接失败 | 串口号错误或未连接 | 检查串口连接和设备管理器 |

## 相关文件
- `<SKILL>/tools/serial-monitor/scripts/serial_monitor.py` - CLI 工具
- `<SKILL>/tools/serial-mcp/` - GUI MCP 工具
- `commands/build.md` - 编译说明
- `commands/flash.md` - 烧录说明
