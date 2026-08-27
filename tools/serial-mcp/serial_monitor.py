#!/usr/bin/env python3
"""
Serial Monitor & AI Assistant (S5核心产品)

MCP工具 + tkinter GUI + pyserial
用于人-AI协作验证流程

依赖:
    pip install pyserial

用法:
    python serial_monitor.py
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import serial
import serial.tools.list_ports
import threading
import json
import os
from datetime import datetime
from pathlib import Path

# 配置路径 - 从环境变量或参数传入
# 项目路径由AI在启动时通过参数传入
# 格式: python serial_monitor.py --project "D:\项目路径"
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--project", help="项目根目录路径")
parser.add_argument("--step", help="当前验证步骤，如s3")
args, unknown = parser.parse_known_args()

# 项目根目录
PROJECT_ROOT = Path(args.project) if args.project else Path.cwd()

# 配置和日志路径（状态目录：.ea 优先，.em 兼容）
def _state_dir(root: Path) -> Path:
    for d in (".ea", ".em"):
        if (root / d).is_dir():
            return root / d
    return root / ".ea"

_STATE_DIR = _state_dir(PROJECT_ROOT)
CONFIG_PATH = _STATE_DIR / "config" / "serial_config.json"
LOG_DIR = _STATE_DIR / "logs"

# 默认配置
DEFAULT_CONFIG = {
    "port": "COM5",
    "baudrate": 115200,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "timeout": 1,
    "timestamp": True,
    "auto_scroll": True
}


class SerialMonitor:
    """串口监控器 + tkinter UI"""

    def __init__(self, root, step=None):
        self.root = root
        self.root.title("Serial Monitor & AI Assistant (S5)")
        self.root.geometry("900x600")

        self.serial_port = None
        self.is_connected = False
        self.is_receiving = False
        self.log_lines = []
        self.current_log_file = None
        self.current_step = step  # 当前验证步骤

        # 加载配置
        self.config = self.load_config()

        # 创建UI
        self.create_ui()

        # 刷新端口列表
        self.refresh_ports()

        # 确保日志目录存在
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        # 启动MCP后台服务
        self.mcp_running = True
        threading.Thread(target=self.mcp_server_loop, daemon=True).start()

    def load_config(self):
        """加载配置"""
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    return {**DEFAULT_CONFIG, **json.load(f)}
            except Exception as e:
                print(f"加载配置失败: {e}")
        return DEFAULT_CONFIG.copy()

    def save_config(self):
        """保存配置"""
        try:
            # 确保目录存在
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"保存配置失败: {e}")

    def create_ui(self):
        """创建UI"""
        # 主容器
        main_frame = ttk.Frame(self.root, padding="5")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # 配置区域
        config_frame = ttk.LabelFrame(main_frame, text="串口配置", padding="5")
        config_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(0, 5))

        # 第一行：端口、刷新按钮
        ttk.Label(config_frame, text="端口:").grid(row=0, column=0, sticky=tk.W)
        self.port_var = tk.StringVar(value=self.config.get("port", "COM5"))
        self.port_combo = ttk.Combobox(config_frame, textvariable=self.port_var, width=8)
        self.port_combo.grid(row=0, column=1, sticky=tk.W, padx=5)

        self.refresh_btn = ttk.Button(config_frame, text="刷新",
                                       command=self.refresh_ports, width=6)
        self.refresh_btn.grid(row=0, column=2, sticky=tk.W)

        # 连接/断开按钮
        self.connect_btn = ttk.Button(config_frame, text="连接",
                                      command=self.toggle_connection)
        self.connect_btn.grid(row=0, column=3, padx=(20, 0))

        # 第二行：波特率、数据位、校验位、停止位
        ttk.Label(config_frame, text="波特率:").grid(row=1, column=0, sticky=tk.W, pady=(5,0))
        self.baud_var = tk.StringVar(value=str(self.config.get("baudrate", 115200)))
        baudrates = ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]
        self.baud_combo = ttk.Combobox(config_frame, textvariable=self.baud_var,
                                        values=baudrates, width=8)
        self.baud_combo.grid(row=1, column=1, sticky=tk.W, padx=5, pady=(5,0))

        ttk.Label(config_frame, text="数据位:").grid(row=1, column=2, sticky=tk.W, pady=(5,0))
        self.bytesize_var = tk.StringVar(value=str(self.config.get("bytesize", 8)))
        bytesizes = ["5", "6", "7", "8"]
        self.bytesize_combo = ttk.Combobox(config_frame, textvariable=self.bytesize_var,
                                            values=bytesizes, width=3)
        self.bytesize_combo.grid(row=1, column=3, sticky=tk.W, pady=(5,0))

        ttk.Label(config_frame, text="校验:").grid(row=2, column=0, sticky=tk.W, pady=(5,0))
        self.parity_var = tk.StringVar(value=self.config.get("parity", "N"))
        parities = ["N", "E", "O", "M", "S"]
        self.parity_combo = ttk.Combobox(config_frame, textvariable=self.parity_var,
                                           values=parities, width=3)
        self.parity_combo.grid(row=2, column=1, sticky=tk.W, pady=(5,0))

        ttk.Label(config_frame, text="停止位:").grid(row=2, column=2, sticky=tk.W, pady=(5,0))
        self.stopbits_var = tk.StringVar(value=str(self.config.get("stopbits", 1)))
        stopbits = ["1", "1.5", "2"]
        self.stopbits_combo = ttk.Combobox(config_frame, textvariable=self.stopbits_var,
                                            values=stopbits, width=3)
        self.stopbits_combo.grid(row=2, column=3, sticky=tk.W, pady=(5,0))

        # 第三行：选项
        self.timestamp_var = tk.BooleanVar(value=self.config.get("timestamp", True))
        ttk.Checkbutton(config_frame, text="时间戳",
                        variable=self.timestamp_var).grid(row=3, column=0, sticky=tk.W, pady=(10,0))

        self.autoscroll_var = tk.BooleanVar(value=self.config.get("auto_scroll", True))
        ttk.Checkbutton(config_frame, text="自动滚屏",
                        variable=self.autoscroll_var).grid(row=3, column=1, sticky=tk.W, pady=(10,0))

        # 清除按钮
        self.clear_btn = ttk.Button(config_frame, text="清除",
                                    command=self.clear_log)
        self.clear_btn.grid(row=3, column=2, sticky=tk.W, pady=(10,0), padx=(10,0))

        # 日志显示区域
        log_frame = ttk.LabelFrame(main_frame, text="日志显示", padding="5")
        log_frame.grid(row=0, column=1, sticky=(tk.W, tk.E, tk.N, tk.S))

        # 搜索栏
        search_frame = ttk.Frame(log_frame)
        search_frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(search_frame, text="搜索:").pack(side=tk.LEFT)
        self.search_entry = ttk.Entry(search_frame, font=("Consolas", 10), width=15)
        self.search_entry.pack(side=tk.LEFT, padx=5)
        self.search_entry.bind("<Return>", lambda e: self.search_text(1))

        ttk.Button(search_frame, text="查找", command=lambda: self.search_text(1)).pack(side=tk.LEFT)
        ttk.Button(search_frame, text="上一个", command=lambda: self.search_text(-1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(search_frame, text="下一个", command=lambda: self.search_text(1)).pack(side=tk.LEFT)

        self.log_text = scrolledtext.ScrolledText(log_frame, width=60, height=25,
                                                   font=("Consolas", 10))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 搜索标记（高亮）配置
        self.log_text.tag_configure("search_match", background="yellow")

        # 发送区域
        send_frame = ttk.LabelFrame(main_frame, text="发送命令", padding="5")
        send_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(5, 0))

        self.send_entry = ttk.Entry(send_frame, font=("Consolas", 10))
        self.send_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.send_entry.bind("<Return>", lambda e: self.send_data())

        self.send_btn = ttk.Button(send_frame, text="发送", command=self.send_data)
        self.send_btn.pack(side=tk.RIGHT)

        # 状态栏
        self.status_var = tk.StringVar(value="未连接")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var,
                               relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E))

        # 配置权重
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(0, weight=1)

    def clear_log(self):
        """清除日志显示"""
        self.log_text.delete(1.0, tk.END)
        self.log_lines = []
        self.log("[系统] 日志已清除")

    def search_text(self, direction=1):
        """搜索日志内容
        direction: 1=下一个, -1=上一个
        """
        # 清除之前的搜索高亮
        self.log_text.tag_remove("search_match", 1.0, tk.END)

        keyword = self.search_entry.get()
        if not keyword:
            return

        # 获取当前内容
        content = self.log_text.get(1.0, tk.END)
        lines = content.split('\n')

        # 查找匹配的起始位置
        current_pos = self.log_text.index(tk.INSERT)
        current_line = int(current_pos.split('.')[0])

        # 搜索
        start_line = current_line + direction
        if start_line < 1:
            start_line = 1
        elif start_line > len(lines):
            start_line = len(lines)

        found = False
        search_line = start_line

        for i in range(len(lines)):
            idx = (search_line - 1 + i * direction) % len(lines)
            if idx < 0:
                idx = len(lines) - 1
            if keyword.lower() in lines[idx].lower():
                search_line = idx + 1
                found = True
                break

        if found:
            # 高亮显示
            start_idx = f"{search_line}.0"
            end_idx = f"{search_line}.{len(lines[search_line-1])}"
            self.log_text.tag_add("search_match", start_idx, end_idx)
            self.log_text.see(start_idx)
            self.log_text.mark_set(tk.INSERT, start_idx)
        else:
            self.status_var.set(f"未找到: {keyword}")

    def refresh_ports(self):
        """刷新可用端口列表"""
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo['values'] = ports if ports else ["无可用端口"]
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])

    def toggle_connection(self):
        """切换连接状态"""
        if self.is_connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        """连接串口"""
        try:
            port = self.port_var.get()
            baudrate = int(self.baud_var.get())
            bytesize_map = {"5": serial.FIVEBITS, "6": serial.SIXBITS,
                           "7": serial.SEVENBITS, "8": serial.EIGHTBITS}
            parity_map = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN,
                         "O": serial.PARITY_ODD, "M": serial.PARITY_MARK,
                         "S": serial.PARITY_SPACE}
            stopbits_map = {"1": serial.STOPBITS_ONE, "1.5": serial.STOPBITS_ONE_POINT_FIVE,
                           "2": serial.STOPBITS_TWO}

            bytesize = bytesize_map.get(self.bytesize_var.get(), serial.EIGHTBITS)
            parity = parity_map.get(self.parity_var.get(), serial.PARITY_NONE)
            stopbits = stopbits_map.get(self.stopbits_var.get(), serial.STOPBITS_ONE)

            self.serial_port = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                timeout=1,
                dsrdtr=False,
                rtscts=False,
                xonxoff=False
            )

            self.is_connected = True
            self.connect_btn.config(text="断开")
            self.status_var.set(f"已连接 {port} @ {baudrate} {bytesize}-{parity}-{stopbits}")

            # 更新配置
            self.config["port"] = port
            self.config["baudrate"] = baudrate
            self.config["bytesize"] = int(self.bytesize_var.get())
            self.config["parity"] = self.parity_var.get()
            self.config["stopbits"] = float(self.stopbits_var.get())
            self.save_config()

            # 创建日志文件
            self.create_log_file()

            # 开始接收线程
            self.is_receiving = True
            self.receive_thread = threading.Thread(target=self.receive_data, daemon=True)
            self.receive_thread.start()

        except Exception as e:
            messagebox.showerror("连接错误", str(e))

    def disconnect(self):
        """断开连接"""
        self.is_connected = False
        self.is_receiving = False

        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()

        self.connect_btn.config(text="连接")
        self.status_var.set("未连接")

    def create_log_file(self):
        """创建日志文件"""
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 格式: serial_<步骤>_<时间戳>.log
        step = self.current_step or "unknown"
        self.current_log_file = LOG_DIR / f"serial_{step}_{timestamp}.log"
        self.log_lines = []
        self.log(f"[系统] 日志文件: {self.current_log_file}")

    def log(self, message):
        """添加日志"""
        self.log_text.insert(tk.END, message + "\n")
        if self.autoscroll_var.get():
            self.log_text.see(tk.END)

    def receive_data(self):
        """接收数据线程"""
        while self.is_receiving and self.serial_port and self.serial_port.is_open:
            try:
                if self.serial_port.in_waiting:
                    line = self.serial_port.readline().decode('latin-1', errors='replace').strip()
                    if line:
                        # 添加时间戳
                        if self.timestamp_var.get():
                            timestamp = datetime.now().strftime("%H:%M:%S")
                            line = f"[{timestamp}] {line}"

                        # 添加到UI
                        self.root.after(0, lambda l=line: self.log(l))

                        # 保存到日志
                        self.log_lines.append(line)

                        # 追加到文件
                        if self.current_log_file:
                            with open(self.current_log_file, 'a', encoding='utf-8') as f:
                                f.write(line + "\n")
            except Exception as e:
                self.root.after(0, lambda: self.status_var.set(f"接收错误: {e}"))
                break

    def send_data(self):
        """发送数据"""
        if not self.is_connected:
            messagebox.showwarning("未连接", "请先连接串口")
            return

        data = self.send_entry.get()
        if not data:
            return

        try:
            # 添加换行符
            self.serial_port.write((data + "\r\n").encode('utf-8'))

            # 显示发送的数据
            self.log(f"[发送] {data}")
            self.send_entry.delete(0, tk.END)
        except Exception as e:
            messagebox.showerror("发送错误", str(e))

    def get_log_content(self):
        """获取日志内容（供MCP调用）"""
        return "\n".join(self.log_lines)

    def get_log_file_path(self):
        """获取日志文件路径（供MCP调用）"""
        return str(self.current_log_file) if self.current_log_file else ""

    def mcp_server_loop(self):
        """MCP后台服务 - 通过文件共享与Claude通信"""
        import sys
        # MCP通信文件路径
        mcp_request_file = LOG_DIR / "mcp_request.json"
        mcp_response_file = LOG_DIR / "mcp_response.json"

        while self.mcp_running:
            try:
                # 检查是否有MCP请求文件
                if mcp_request_file.exists():
                    try:
                        with open(mcp_request_file, 'r', encoding='utf-8') as f:
                            request = json.load(f)

                        # 删除请求文件
                        mcp_request_file.unlink()

                        # 处理请求
                        response = self.handle_mcp_request(request)

                        # 写入响应
                        with open(mcp_response_file, 'w', encoding='utf-8') as f:
                            json.dump(response, f, ensure_ascii=False)

                    except Exception as e:
                        # 写入错误响应
                        try:
                            with open(mcp_response_file, 'w', encoding='utf-8') as f:
                                json.dump({"success": False, "error": str(e)}, f)
                        except:
                            pass
                else:
                    # 没有请求时短暂休眠，避免CPU占用过高
                    import time
                    time.sleep(0.1)

            except Exception as e:
                import time
                time.sleep(1)  # 出错时稍长等待

    def handle_mcp_request(self, request: dict) -> dict:
        """处理MCP请求"""
        method = request.get("method")
        params = request.get("params", {})

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "tools": self.get_mcp_tools()
                }
            }
        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            result = self.call_mcp_tool(tool_name, arguments)
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

    def get_mcp_tools(self) -> list:
        """返回MCP工具列表"""
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
                "description": "获取当前日志文件路径和内容",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            }
        ]

    def call_mcp_tool(self, name: str, arguments: dict) -> dict:
        """调用MCP工具"""
        if name == "serial_read":
            return self._mcp_read_serial(arguments.get("lines", 100))
        elif name == "serial_status":
            return self._mcp_get_status()
        elif name == "serial_send":
            return self._mcp_send_command(arguments.get("command", ""))
        elif name == "serial_log_file":
            return self._mcp_get_log_file()
        else:
            return {"success": False, "error": f"未知工具: {name}"}

    def _mcp_read_serial(self, lines: int) -> dict:
        """MCP: 读取串口日志"""
        try:
            log_content = self.get_log_content()
            log_lines = log_content.split("\n") if log_content else []
            recent_lines = log_lines[-lines:] if lines > 0 else log_lines
            return {
                "success": True,
                "data": recent_lines,
                "line_count": len(recent_lines),
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _mcp_get_status(self) -> dict:
        """MCP: 获取状态"""
        return {
            "success": True,
            "connected": self.is_connected,
            "port": self.port_var.get(),
            "baudrate": self.baud_var.get(),
            "bytesize": self.bytesize_var.get(),
            "parity": self.parity_var.get(),
            "stopbits": self.stopbits_var.get(),
            "log_file": self.get_log_file_path(),
            "total_lines": len(self.log_lines)
        }

    def _mcp_send_command(self, command: str) -> dict:
        """MCP: 发送命令"""
        if not self.is_connected:
            return {"success": False, "error": "串口未连接"}
        try:
            self.serial_port.write((command + "\r\n").encode('utf-8'))
            self.log(f"[MCP发送] {command}")
            return {"success": True, "sent": command}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _mcp_get_log_file(self) -> dict:
        """MCP: 获取日志文件"""
        log_file = self.get_log_file_path()
        if not log_file:
            return {"success": False, "error": "日志文件未创建"}
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
            return {"success": False, "error": str(e)}

    def close(self):
        """关闭"""
        self.mcp_running = False
        self.disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = SerialMonitor(root, step=args.step)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.close(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
