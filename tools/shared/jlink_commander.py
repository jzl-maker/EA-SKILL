#!/usr/bin/env python
"""JLink Commander 调用基础（共享）。

`jlink-debug` 和 `flash-jlink` 都要起 JLink.exe 跑 Commander 脚本。这套调用里有不少
非显然的加固点（见 `run_commander` 的 docstring），只写一遍 —— 复制第二份意味着
下次加固时又要记得改两处。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

DEFAULT_DEVICE = "N32G4FRRE"
DEFAULT_INTERFACE = "SWD"
DEFAULT_SPEED = 4000

# Commander 的输出上限。`connect` 失败时它会退回交互式提示并持续向 stdout 吐字符，
# 实测刷到过 189MB —— 不设上限会把内存吃光。
MAX_CAPTURE_BYTES = 8 << 20

try:
    from tool_config import get_tool_path
except ImportError:                      # 被独立导入（shared 未进 sys.path）时降级
    get_tool_path = None  # type: ignore


def find_jlink(explicit: str | None = None) -> tuple[str | None, Path | None]:
    """返回 (JLink.exe 路径, J-Link 安装目录)，找不到返回 (None, None)。"""
    if explicit:
        p = Path(explicit)
        if p.exists():
            return str(p), p.parent
        return None, None
    configured = get_tool_path("jlink") if get_tool_path else None
    if configured and Path(configured).exists():
        return str(Path(configured)), Path(configured).parent
    found = shutil.which("JLink.exe") or shutil.which("JLink")
    if found:
        p = Path(found)
        return str(p), p.parent
    return None, None


def build_commander_script(device: str, interface: str, speed: int,
                           body: list[str]) -> str:
    """生成自包含的 JLink Commander 脚本。"""
    lines = [
        f"si {interface}",
        f"speed {speed}",
        f"device {device}",
        "connect",
        *body,
        "exit",
    ]
    return "\n".join(lines) + "\n"


def run_commander(
    jlink_exe: str,
    script: str,
    timeout: int = 60,
    device: str = DEFAULT_DEVICE,
    interface: str = DEFAULT_INTERFACE,
    speed: int = DEFAULT_SPEED,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """执行 JLink Commander 脚本文件，返回 CompletedProcess（stdout 为已解码文本）。

    JLink < V7.60 不支持 `-CommanderScript -`（stdin），故仍写临时脚本文件。

    加固点（旧实现用 `capture_output=True` 无 stdin 重定向，实测刷到 189MB 并
    全部缓进内存）：

    1. `stdin=DEVNULL` —— `connect` 失败时 Commander 会退回交互式提示并持续向
       stdout 吐字符；stdin 若继承父进程管道则永远读不到 EOF，刷屏不止。
    2. 命令行显式带 `-Device/-If/-Speed/-AutoConnect 1/-ExitOnError 1` —— 脚本内
       的 `device/si/speed/connect` 保留（冗余但无害），实测带命令行参数才稳定。
    3. stdout 落临时文件再截断读取，超大输出不进内存。
    4. 显式 `encoding="utf-8"` —— 否则按 locale(cp936) 解码，非 ASCII 抛
       UnicodeDecodeError。
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".jlink", prefix="ea_skill_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        cmd = [
            jlink_exe,
            "-Device", device,
            "-If", interface,
            "-Speed", str(speed),
            "-AutoConnect", "1",
            "-ExitOnError", "1",
            "-CommanderScript", tmp_path,
        ]
        with tempfile.TemporaryFile() as sink:
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                cwd=cwd,
            )
            sink.seek(0)
            raw = sink.read(MAX_CAPTURE_BYTES + 1)
        truncated = len(raw) > MAX_CAPTURE_BYTES
        if truncated:
            raw = raw[:MAX_CAPTURE_BYTES]
        out = raw.decode("utf-8", errors="replace")
        if truncated:
            out += (f"\n[ea-skill] ⚠️ JLink 输出超过 {MAX_CAPTURE_BYTES >> 20}MB 已截断"
                    "（通常意味着 connect 失败后退回了交互式提示）")
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, "")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def to_script_path(p: str | Path) -> str:
    """转成 Commander 脚本里可用的路径：一律正斜杠。

    JLink 脚本里的 `\\` 会被当转义（`\\n`、`\\t` 在老版本上尤其危险），
    正斜杠在 Windows 上同样可用。
    """
    return str(Path(p).resolve()).replace("\\", "/")
