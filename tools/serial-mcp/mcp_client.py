#!/usr/bin/env python3
"""
MCP客户端示例

用于测试MCP服务器接口
"""

import json
import subprocess
import sys
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def call_mcp_server(tool_name: str, arguments: dict = None) -> dict:
    """调用MCP服务器"""

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments or {}
        }
    }

    # 启动MCP服务器进程
    mcp_script = Path(__file__).parent / "mcp_server.py"

    try:
        process = subprocess.Popen(
            [sys.executable, str(mcp_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        stdout, stderr = process.communicate(
            input=json.dumps(request) + "\n",
            timeout=5
        )

        if stderr:
            print(f"STDERR: {stderr}", file=sys.stderr)

        if stdout:
            return json.loads(stdout.strip())
        else:
            return {"error": "无输出"}

    except subprocess.TimeoutExpired:
        process.kill()
        return {"error": "超时"}
    except Exception as e:
        return {"error": str(e)}


def main():
    """测试MCP接口"""

    print("=== Serial MCP Client Test ===\n")

    # 测试1: serial_status
    print("1. 测试 serial_status:")
    result = call_mcp_server("serial_status")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    # 测试2: serial_read
    print("\n2. 测试 serial_read:")
    result = call_mcp_server("serial_read", {"lines": 10})
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
