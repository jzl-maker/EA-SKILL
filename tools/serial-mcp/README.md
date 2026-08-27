# Serial Monitor & AI Assistant (S5)

人-AI协作验证工具，MCP工具+tkinter GUI。

## 功能

- 串口配置（端口、波特率）
- 实时显示串口数据
- 发送命令到串口
- 日志保存到文件
- MCP接口供AI调用

## 安装

```bash
pip install -r requirements.txt
```

## 使用

### 启动GUI

```bash
python serial_monitor.py
```

### MCP接口

```bash
python mcp_server.py
```

## 目录结构

```
serial-mcp/
├── serial_monitor.py   # 主程序（tkinter UI）
├── mcp_server.py       # MCP服务器
├── mcp_client.py       # MCP客户端示例
├── requirements.txt    # 依赖
└── README.md          # 本文件
```

## MCP工具

| 工具 | 说明 |
|------|------|
| serial_read | 读取串口日志 |
| serial_status | 获取连接状态 |
| serial_send | 发送命令 |
| serial_log_file | 获取日志文件 |
