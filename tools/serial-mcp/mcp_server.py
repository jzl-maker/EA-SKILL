#!/usr/bin/env python3
"""
Serial MCP Server

MCP接口，供Claude Code调用读取串口数据

用法:
    python mcp_server.py
    或
    from mcp_server import SerialMCPServer
    server = SerialMCPServer()
    server.run()
"""

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# MCP协议支持（简化版，不依赖官方SDK）
# 如果需要完整MCP支持，使用: pip install mcp

try:
    from serial_monitor import SerialMonitor, LOG_DIR
except ImportError:
    # 独立运行时
    import sys
    import os
    sys.path.insert(0, str(Path(__file__).parent))
    from serial_monitor import SerialMonitor, LOG_DIR


class SerialMCPServer:
    """串口MCP服务器"""

    def __init__(self):
        self.monitor: Optional[SerialMonitor] = None
        self.running = False

    def set_monitor(self, monitor: SerialMonitor):
        """设置关联的SerialMonitor实例"""
        self.monitor = monitor

    def get_tools(self) -> List[dict]:
        """返回可用工具列表"""
        return [
            {
                "name": "serial_read",
                "description": "读取串口缓冲区数据和日志",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "lines": {
                            "type": "number",
                            "description": "返回最近N行日志（默认100）",
                            "default": 100
                        }
                    }
                }
            },
            {
                "name": "serial_status",
                "description": "获取串口连接状态",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "serial_send",
                "description": "通过串口发送命令",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "要发送的命令"
                        }
                    },
                    "required": ["command"]
                }
            },
            {
                "name": "serial_log_file",
                "description": "获取当前日志文件路径",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            }
        ]

    def call_tool(self, name: str, arguments: dict) -> dict:
        """调用工具"""
        if not self.monitor:
            return {
                "success": False,
                "error": "SerialMonitor未初始化"
            }

        if name == "serial_read":
            return self._read_serial(arguments.get("lines", 100))
        elif name == "serial_status":
            return self._get_status()
        elif name == "serial_send":
            return self._send_command(arguments.get("command", ""))
        elif name == "serial_log_file":
            return self._get_log_file()
        else:
            return {
                "success": False,
                "error": f"未知工具: {name}"
            }

    def _read_serial(self, lines: int) -> dict:
        """读取串口日志"""
        try:
            log_content = self.monitor.get_log_content()
            log_lines = log_content.split("\n") if log_content else []
            recent_lines = log_lines[-lines:] if lines > 0 else log_lines

            return {
                "success": True,
                "data": recent_lines,
                "line_count": len(recent_lines),
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def _get_status(self) -> dict:
        """获取状态"""
        if not self.monitor:
            return {
                "connected": False,
                "error": "SerialMonitor未初始化"
            }

        return {
            "success": True,
            "connected": self.monitor.is_connected,
            "port": self.monitor.port_var.get(),
            "baudrate": self.monitor.baud_var.get(),
            "bytesize": self.monitor.bytesize_var.get(),
            "parity": self.monitor.parity_var.get(),
            "stopbits": self.monitor.stopbits_var.get(),
            "log_file": self.monitor.get_log_file_path(),
            "total_lines": len(self.monitor.log_lines)
        }

    def _send_command(self, command: str) -> dict:
        """发送命令"""
        if not self.monitor:
            return {
                "success": False,
                "error": "SerialMonitor未初始化"
            }

        if not self.monitor.is_connected:
            return {
                "success": False,
                "error": "串口未连接"
            }

        try:
            # 通过monitor发送
            self.monitor.serial_port.write((command + "\r\n").encode('utf-8'))
            self.monitor.log(f"[MCP发送] {command}")

            return {
                "success": True,
                "sent": command
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def _get_log_file(self) -> dict:
        """获取日志文件"""
        if not self.monitor:
            return {
                "success": False,
                "error": "SerialMonitor未初始化"
            }

        log_file = self.monitor.get_log_file_path()

        if not log_file:
            return {
                "success": False,
                "error": "日志文件未创建"
            }

        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                content = f.read()

            return {
                "success": True,
                "path": log_file,
                "content": content,
                "line_count": len(content.split("\n"))
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


# MCP协议处理（基于stdio）
def handle_mcp_request(request: dict, server: SerialMCPServer) -> dict:
    """处理MCP请求"""
    method = request.get("method")
    params = request.get("params", {})

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "tools": server.get_tools()
            }
        }
    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        result = server.call_tool(tool_name, arguments)
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": result
        }
    else:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}"
            }
        }


def main():
    """独立运行MCP服务器"""
    import sys

    server = SerialMCPServer()

    # 从stdin读取请求，输出到stdout
    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
            response = handle_mcp_request(request, server)
            print(json.dumps(response), flush=True)
        except json.JSONDecodeError:
            continue
        except Exception as e:
            print(json.dumps({
                "jsonrpc": "2.0",
                "error": {
                    "code": -32603,
                    "message": str(e)
                }
            }), flush=True)


if __name__ == "__main__":
    main()
