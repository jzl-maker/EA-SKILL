#!/usr/bin/env python
"""通用嵌入式调试工具（RTT 取证 + 断点定位 + 内存监视 + 寄存器 + 复位放行）。

为 `jlink-debug` skill 提供可重复调用的执行入口，支持：

- `--rtt snapshot` RTT **取证**：savebin 直读 RAM 控制块+环形缓冲，拿回含**历史**的日志
- `--rtt start/stop/show`  RTT 实时日志（JLinkRTTLogger，只能抓 attach 之后的新数据）
- `--bp`          断点定位「代码跑哪了」（.map 符号 → SetBP → go → halt → PC/regs）
- `--mem`         内存变量（mem32/mem8；`--no-halt` 走 savebin 后台读，不打断目标）
- `--regs`        读 CPU 寄存器（PC/SP/LR/…）
- `--reset-run`   复位并放行目标（`r`+`g`），配合人工交互测试
- `--gdb`         源码级调试（可选：检测 arm-none-eabi-gdb + JLinkGDBServerCL）

双后端：
- `--backend jlink`（默认）：J-Link Commander 脚本
- `--backend openocd`：OpenOCD TCL 端口（ST-Link/CMSIS-DAP/J-Link 通用）。
  --mem/--regs 走 TCL mdw/reg 命令，**运行中读取不 halt CPU**（无停机实时监控）。
  --bp/--rtt/--gdb/--reset-run 在 OpenOCD 后端不支持（用 --backend jlink）。

非侵入能力说明：JLink Commander 的 `connect`（`-AutoConnect 1`）既不复位也不 halt；
`savebin` 走 AHB-AP 后台访问，目标全速运行时亦可读写 RAM。`--rtt snapshot` 与
`--mem --no-halt` 即建立在此之上，是「目标正在跑人工测试时取证据」的唯一可靠通道。

零第三方依赖：只调用 J-Link / OpenOCD 可执行文件。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# 引入 tool_config 以支持配置的工具路径
_SCRIPT_DIR = Path(__file__).resolve().parent
for _candidate in [_SCRIPT_DIR / "shared", _SCRIPT_DIR.parent / "shared",
                   _SCRIPT_DIR.parents[1] / "shared", _SCRIPT_DIR.parents[2] / "shared"]:
    if (_candidate / "tool_config.py").exists():
        sys.path.insert(0, str(_candidate))
        break
try:
    from tool_config import get_tool_path
except ImportError:
    get_tool_path = None  # type: ignore

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEFAULT_DEVICE = "N32G4FRRE"
DEFAULT_INTERFACE = "SWD"
DEFAULT_SPEED = 4000
DEFAULT_RTT_CHANNEL = 0
DEFAULT_RUN_MS = 2000
RTT_PID_FILE = Path(os.environ.get("TEMP", ".")) / "ea_skill_rtt.pid"

# JLink Commander 输出捕获上限。connect 失败时 JLink 会退回交互式提示并狂刷字符，
# 实测产生过 189MB 输出；成功输出仅数十 KB，故截断头部足够覆盖结果。
MAX_CAPTURE_BYTES = 8 << 20
# RTT 控制块探测长度：acID[16] + NumUp(4) + NumDown(4) + aUp[0](24) = 48，取 64 留余量。
# 不按结构体全量读（aUp/aDown 数量随编译期配置变），避免越界到 RAM 边界外。
RTT_CB_PROBE = 0x40
RTT_CB_ID = b"SEGGER RTT"
# SEGGER_RTT_CB 中 aUp[0] 的字段偏移（相对控制块起始）
RTT_AUP_OFFSET = 24
# 会独占 J-Link 的进程：连不上目标时按此排查，避免误判成硬件问题
JLINK_OCCUPYING_IMAGES = (
    "JLinkRTTLogger.exe",
    "JLinkGDBServerCL.exe",
    "JLink.exe",
    "UV4.exe",
)
SAVEBIN_TIMEOUT = 40

# OpenOCD 后端
DEFAULT_OCD_PORT = 6666
OCD_INTERFACE_CONFIGS = {
    "stlink": "interface/stlink.cfg",
    "cmsis-dap": "interface/cmsis-dap.cfg",
    "jlink": "interface/jlink.cfg",
}
OCD_INTERFACE_PRIORITY = ["stlink", "cmsis-dap", "jlink"]
OCD_PID_FILE = Path(os.environ.get("TEMP", ".")) / "ea_skill_ocd.pid"

# .map Global Symbols 段符号行
# 格式: <Symbol Name> <Value> <Type> <Size> <Object(Section)>
_MAP_SYMBOL_RE = re.compile(
    r"^(\S+)\s+(0x[0-9A-Fa-f]+)\s+(Thumb Code|Code|Data|Number|Section|Thumb Function)"
    r"\s+(\d+)\s+(.+)$"
)
_REG_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)(?:\([A-Za-z0-9]+\))?\s*=\s*(0x[0-9A-Fa-f]+|[0-9A-Fa-f]+)")
_MEM32_LINE_RE = re.compile(r"^([0-9A-Fa-f]+)\s*=\s*(.*)$")

# ---------------------------------------------------------------------------
# 工具路径解析
# ---------------------------------------------------------------------------

def _exe_path(jlink_dir: Path, name: str) -> str | None:
    p = jlink_dir / name
    if p.exists():
        return str(p)
    return None

def find_jlink(explicit: str | None = None) -> tuple[str | None, Path | None]:
    """返回 (jlink_exe 路径, jlink 安装目录)，找不到返回 (None, None)。"""
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

def find_peers() -> dict[str, str | None]:
    """探测 J-Link 同目录的 RTT Logger / GDB Server / gdb 工具链。"""
    _, jlink_dir = find_jlink()
    result = {
        "jlink": None,
        "rtt_logger": None,
        "gdb_server": None,
        "gdb": None,
    }
    if jlink_dir is None:
        return result
    result["jlink"] = _exe_path(jlink_dir, "JLink.exe")
    result["rtt_logger"] = _exe_path(jlink_dir, "JLinkRTTLogger.exe")
    result["gdb_server"] = _exe_path(jlink_dir, "JLinkGDBServerCL.exe")
    for gdb in ("arm-none-eabi-gdb", "arm-eabi-gdb"):
        g = shutil.which(gdb)
        if g:
            result["gdb"] = g
            break
    return result

# ---------------------------------------------------------------------------
# .map 符号解析
# ---------------------------------------------------------------------------

def find_map_file(workspace: str | Path | None = None) -> Path | None:
    """扫描工作区（默认 cwd）中的 .map 符号文件。"""
    base = Path(workspace) if workspace else Path.cwd()
    for p in base.rglob("*.map"):
        try:
            if p.stat().st_size > 0:
                return p
        except OSError:
            continue
    return None

def parse_map_symbols(map_path: str | Path) -> dict[str, dict[str, Any]]:
    """解析 Keil .map 的 Image Symbol Table 段，返回 {name: {addr, type, size, obj}}。

    Keil .map 的符号表分 Local Symbols（static 变量/局部符号）和
    Global Symbols（全局符号/函数）两个子段，均位于 "Image Symbol Table" 之下。
    只解析 Global Symbols 段会漏掉 static 变量（如老化测试的 total_count），
    故从 "Image Symbol Table" 开始解析到 "Memory Map of the image" 为止。

    Thumb 函数地址取 value & ~1（符号表里带 bit0，真实地址需去除）。
    """
    symbols: dict[str, dict[str, Any]] = {}
    path = Path(map_path)
    if not path.exists():
        return symbols
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return symbols

    in_symbols = False
    for line in text.splitlines():
        stripped = line.strip()
        if "Image Symbol Table" in stripped:
            in_symbols = True
            continue
        if in_symbols and "Memory Map of the image" in stripped:
            break
        if not in_symbols:
            continue
        m = _MAP_SYMBOL_RE.match(stripped)
        if not m:
            continue
        name, value, typ, size, obj = m.groups()
        addr = int(value, 16)
        if typ in ("Thumb Code", "Thumb Function") and addr & 1:
            addr &= ~1  # Thumb bit
        if addr == 0 or "undefined" in typ.lower():
            continue
        # 跳过 section 伪符号（.data/.bss/.text）和编译器内部符号（$ / __ 开头）
        if name.startswith(".") or name.startswith("$") or name.startswith("__"):
            continue
        symbols[name] = {
            "addr": addr,
            "value": value,
            "type": typ,
            "size": int(size),
            "object": obj,
        }
    return symbols

def resolve_symbol(name: str, symbols: dict[str, dict[str, Any]]) -> list[tuple[str, dict]]:
    """按符号名解析：精确匹配优先，其次子串模糊匹配。"""
    if name in symbols:
        return [(name, symbols[name])]
    low = name.lower()
    fuzzy = [(k, v) for k, v in symbols.items() if low in k.lower() or k.lower() in low]
    fuzzy.sort(key=lambda item: len(item[0]))
    return fuzzy

def parse_address(token: str, symbols: dict[str, dict[str, Any]]) -> int | None:
    """把用户输入解析为地址：0x 字面量 或 符号名。"""
    token = token.strip()
    if token.lower().startswith("0x"):
        try:
            return int(token, 16)
        except ValueError:
            return None
    matches = resolve_symbol(token, symbols)
    if not matches:
        return None
    # 优先选择 Data/Code 段且非 Section 的符号
    for _name, info in matches:
        if info["type"] not in ("Section",):
            return info["addr"]
    return matches[0][1]["addr"]

# ---------------------------------------------------------------------------
# OpenOCD TCL 后端（ST-Link / CMSIS-DAP / J-Link 通用，运行中读不 halt）
# ---------------------------------------------------------------------------

def _get_openocd_exe() -> str | None:
    """OpenOCD 可执行路径：tool_config > PATH。"""
    if get_tool_path:
        cfg = get_tool_path("openocd")
        if cfg and Path(cfg).exists():
            return cfg
    return shutil.which("openocd")


def ocd_detect_probe(exe: str) -> str | None:
    """用 `init; exit` 探测已连接的探针类型（stlink 优先）。"""
    for iface in OCD_INTERFACE_PRIORITY:
        cfg = OCD_INTERFACE_CONFIGS[iface]
        try:
            res = subprocess.run([exe, "-f", cfg, "-c", "init; exit"],
                                 capture_output=True, text=True, timeout=8)
        except Exception:
            continue
        combined = f"{res.stdout}\n{res.stderr}".lower()
        if res.returncode == 0 or any(kw in combined for kw in ("cmsis-dap", "st-link", "j-link")):
            return iface
        if any(kw in combined for kw in ("open failed", "no device found")):
            continue
    return None


def _ocd_tcl(port: int, cmd: str, timeout: float = 3.0) -> str | None:
    """OpenOCD TCL 端口发命令收响应（终止符 \x1a）。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            s.sendall((cmd + "\x1a").encode("ascii"))
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\x1a" in chunk:
                    break
            return data.decode("utf-8", errors="replace").replace("\x1a", "").strip()
    except OSError as exc:
        return f"__TCL_ERR__ {exc}"


def ocd_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def ocd_start(iface: str | None, target: str, port: int) -> tuple[subprocess.Popen | None, bool]:
    """确保 OpenOCD 在运行。已监听则复用；否则自动拉起。

    返回 (proc, reused)；proc 非 None 表示本次拉起、退出时需 terminate。
    """
    exe = _get_openocd_exe()
    if not exe:
        print("❌ 未找到 openocd（请 /ea setup 注册，或加入 PATH）")
        return None, False
    if ocd_port_open(port):
        print(f"ℹ️ 复用已运行的 OpenOCD（TCL 端口 {port}）")
        return None, True

    if iface is None:
        iface = ocd_detect_probe(exe)
        if iface:
            print(f"ℹ️ 自动检测到探针: {iface}")
        else:
            print("❌ 未检测到调试探针（--ocd-if stlink|cmsis-dap|jlink 显式指定）")
            return None, False

    icfg = OCD_INTERFACE_CONFIGS.get(iface)
    if not icfg:
        print(f"❌ 不支持的接口: {iface}（支持 stlink / cmsis-dap / jlink）")
        return None, False

    cmd = [exe, "-f", icfg, "-f", target, "-c", f"tcl_port {port}", "-c", "telnet_port 0",
           "-c", "init"]
    print(f"🟢 启动 OpenOCD: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    OCD_PID_FILE.write_text(str(proc.pid), encoding="utf-8")

    # 等待 TCL 端口就绪（最多 8s）
    for _ in range(16):
        if ocd_port_open(port):
            return proc, False
        time.sleep(0.5)
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
    print("❌ OpenOCD 启动超时（探针未连 / target 配置不符）")
    return None, False


def ocd_stop(proc: subprocess.Popen | None) -> None:
    """终止本次拉起的 OpenOCD（复用的不动）。"""
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        OCD_PID_FILE.unlink()
    except OSError:
        pass
    print("🛑 已停止本次拉起的 OpenOCD")


def ocd_read_mem(port: int, addr: int, count: int, width: int) -> dict[int, int] | None:
    """TCL mdw/mdh/mdb 读内存（不 halt）。返回 {addr: value}。"""
    cmd = {32: "mdw", 16: "mdh", 8: "mdb"}.get(width)
    if not cmd:
        return None
    resp = _ocd_tcl(port, f"{cmd} 0x{addr:08X} {count}")
    if not resp or resp.startswith("__TCL_ERR__"):
        return None
    values: dict[int, int] = {}
    hexlen = width // 4
    for line in resp.splitlines():
        m = re.match(r"0x([0-9a-fA-F]+):\s*(.*)", line)
        if not m:
            continue
        base = int(m.group(1), 16)
        for i, w in enumerate(re.findall(rf"[0-9A-Fa-f]{{{hexlen}}}", m.group(2))):
            values[base + i * (width // 8)] = int(w, 16)
    return values


def ocd_read_regs(port: int) -> dict[str, int]:
    """TCL `reg` 读寄存器。输出形如 `(r0) = 0x00000000`。"""
    resp = _ocd_tcl(port, "reg")
    if not resp or resp.startswith("__TCL_ERR__"):
        return {}
    regs: dict[str, int] = {}
    aliases = {"sp": "R13", "lr": "R14", "pc": "R15", "xpsr": "xPSR"}
    for line in resp.splitlines():
        m = re.match(r"\((\S+)\)\s*=\s*0x([0-9a-fA-F]+)", line.strip())
        if not m:
            continue
        name, val = m.group(1), int(m.group(2), 16)
        regs[aliases.get(name.lower(), name)] = val
    return regs


# ---------------------------------------------------------------------------
# JLink Commander 脚本执行
# ---------------------------------------------------------------------------

def build_commander_script(
    device: str,
    interface: str,
    speed: int,
    body: list[str],
) -> str:
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


def diagnose_connect_failure(output: str) -> None:
    """连接失败时排查「J-Link 被占用」，避免误判为硬件问题。

    残留的 RTTLogger / GDBServer / Keil 会独占 J-Link，此时任何 --bp/--mem/--regs
    都报「无法连接目标」，与探针没插、芯片没供电的现象完全一样。
    """
    hit = [img for img in JLINK_OCCUPYING_IMAGES if list_pids(img)]
    print("🔎 连接失败排查：")
    if hit:
        print(f"   ⚠️ 检测到可能独占 J-Link 的进程：{', '.join(hit)}")
        for img in hit:
            pids = list_pids(img)
            print(f"      {img}  PID {', '.join(map(str, pids))}")
        print("      先关掉上面这些（或 --rtt stop）再重试")
    else:
        print("   ✅ 无占用进程 → 检查 USB 连接、芯片供电、SWD 接线")
    if "interactive" in output.lower() or output.count("\n") > 2000:
        print("   ⚠️ 输出异常长，JLink 可能已退回交互式提示（脚本中途报错）")
    print(f"   原始输出尾部：\n{output[-600:]}")

def parse_regs(output: str) -> dict[str, int]:
    """解析 JLink `regs` 输出（R0-R15, xPSR, PRIMASK, FPS*）。

    JLink 的 regs 输出值不带 0x 前缀（如 `R0 = 00000000`），
    且一行内逗号分隔多个寄存器（R0..R15, xPSR），故用非锚定 finditer。
    """
    _ALIASES = {"XPSR": "xPSR"}
    regs: dict[str, int] = {}
    for m in _REG_RE.finditer(output):
        name, val = m.group(1), m.group(2)
        name = _ALIASES.get(name, name)
        try:
            regs[name] = int(val, 16)
        except ValueError:
            pass
    return regs

def parse_mem32(output: str) -> dict[int, int]:
    """解析 JLink `mem32` 输出，返回 {addr: value32}。"""
    values: dict[int, int] = {}
    for line in output.splitlines():
        m = _MEM32_LINE_RE.match(line.strip())
        if not m:
            continue
        try:
            base = int(m.group(1), 16)
        except ValueError:
            continue
        words = re.findall(r"[0-9A-Fa-f]{8}", m.group(2))
        for i, w in enumerate(words):
            try:
                values[base + i * 4] = int(w, 16)
            except ValueError:
                continue
    return values

def parse_mem8(output: str) -> dict[int, int]:
    """解析 JLink `mem8` 输出，返回 {addr: byte}。"""
    values: dict[int, int] = {}
    for line in output.splitlines():
        m = _MEM32_LINE_RE.match(line.strip())
        if not m:
            continue
        try:
            base = int(m.group(1), 16)
        except ValueError:
            continue
        bytes_ = re.findall(r"[0-9A-Fa-f]{2}", m.group(2))
        for i, b in enumerate(bytes_):
            try:
                values[base + i] = int(b, 16)
            except ValueError:
                continue
    return values

# ---------------------------------------------------------------------------
# 进程占用排查（J-Link 被残留 logger/GDBServer/Keil 独占时的误判防护）
# ---------------------------------------------------------------------------

def list_pids(image_name: str) -> list[int]:
    """按镜像名列出进程 PID（Windows tasklist；POSIX pgrep 兜底）。"""
    if os.name == "nt":
        try:
            res = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=10,
            )
        except Exception:
            return []
        pids = []
        for line in res.stdout.splitlines():
            m = re.match(r'"([^"]+)","(\d+)"', line.strip())
            if m and m.group(1).lower() == image_name.lower():
                pids.append(int(m.group(2)))
        return pids
    try:
        res = subprocess.run(["pgrep", "-x", image_name],
                             capture_output=True, text=True, timeout=10)
        return [int(x) for x in res.stdout.split() if x.strip().isdigit()]
    except Exception:
        return []

def kill_pid(pid: int) -> bool:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, text=True, timeout=10)
        else:
            os.kill(pid, 9)
        return True
    except Exception:
        return False

# ---------------------------------------------------------------------------
# savebin 非侵入读 RAM（RTT 取证 / --mem --no-halt 的共同底座）
# ---------------------------------------------------------------------------

def _run_savebin(
    jlink_exe: str,
    specs: list[tuple[str, int, int]],
    device: str,
    interface: str,
    speed: int,
    timeout: int = SAVEBIN_TIMEOUT,
) -> dict[str, bytes]:
    """批量 savebin 读 RAM，返回 {文件名: 内容}（读取失败的文件不出现）。

    **非侵入**：`savebin` 走 AHB-AP 后台访问，不 halt CPU、不复位。配合
    `-AutoConnect 1`（`connect` 既不复位也不 halt，只打印 `CPU is not halted !`），
    可在目标**全速运行时**反复读取 —— 这是 RTT 拿历史日志与实时采样能成立的根据。

    specs 里的地址/长度经命令行传给 savebin；脚本只含 savebin 行 + `qc`，
    连接由命令行 `-AutoConnect 1` 完成。
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="ea_rtt_"))
    try:
        if " " in str(tmpdir):
            print(f"⚠️ 临时目录含空格，savebin 可能写入失败: {tmpdir}")
        lines = [f"savebin {tmpdir / name} 0x{addr:08X} 0x{size:X}"
                 for name, addr, size in specs]
        try:
            run_commander(jlink_exe, "\n".join(lines) + "\nqc\n", timeout=timeout,
                          device=device, interface=interface, speed=speed)
        except subprocess.TimeoutExpired:
            return {}
        got: dict[str, bytes] = {}
        for name, _addr, size in specs:
            p = tmpdir / name
            try:
                if p.exists() and p.stat().st_size == size:
                    got[name] = p.read_bytes()
            except OSError:
                continue
        return got
    except OSError:
        return {}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def savebin_read(
    jlink_exe: str,
    addr: int,
    size: int,
    device: str = DEFAULT_DEVICE,
    interface: str = DEFAULT_INTERFACE,
    speed: int = DEFAULT_SPEED,
) -> bytes | None:
    """非侵入读取一段 RAM（不 halt CPU）。失败返回 None。"""
    return _run_savebin(jlink_exe, [("read.bin", addr, size)],
                        device, interface, speed).get("read.bin")

# ---------------------------------------------------------------------------
# RTT 日志抓取（JLinkRTTLogger）
# ---------------------------------------------------------------------------

def rtt_start(device: str, interface: str, speed: int, channel: int,
              log_file: str | Path, force: bool = False) -> int:
    """后台启动 JLinkRTTLogger，返回 PID。

    启动前检查是否已有 logger 在跑：残留 logger 会独占 J-Link，导致后续
    `--bp/--mem/--regs` 全部报「无法连接目标」而被误判成硬件故障。
    """
    peers = find_peers()
    if not peers["rtt_logger"]:
        print("❌ 未找到 JLinkRTTLogger.exe（J-Link 安装目录）")
        return 1

    existing = [p for p in list_pids("JLinkRTTLogger.exe")]
    if existing:
        print(f"⚠️ 已有 JLinkRTTLogger 在运行 (PID {', '.join(map(str, existing))})")
        print("   它会独占 J-Link → 之后 --bp/--mem/--regs 都会报「无法连接目标」")
        if not force:
            print("   先 `--rtt stop` 停掉，或加 --force 强制清理")
            return 1
        for pid in existing:
            kill_pid(pid)
        print(f"   已强制清理 {len(existing)} 个残留 logger")

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [peers["rtt_logger"],
         "-Device", device,
         "-if", interface,
         "-Speed", str(speed),
         "-RTTChannel", str(channel),
         str(log_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    RTT_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    print(f"🟢 RTT Logger 已启动 (PID {proc.pid}) → {log_path}")
    print(f"   通道 {channel} | {device} {interface}@{speed}")
    print("   ⚠️ RTTLogger 只抓 attach 之后的新数据，拿不到已发生的历史日志")
    print("      → 要历史/要取证请用 `--rtt snapshot`；本方式先 start 再复位才有效")
    return 0

def rtt_stop() -> int:
    """停止 JLinkRTTLogger：优先 PID 文件，兜底按镜像名清理残留。"""
    pids: list[int] = []
    if RTT_PID_FILE.exists():
        try:
            pids = [int(RTT_PID_FILE.read_text(encoding="utf-8").strip())]
        except (OSError, ValueError):
            pids = []

    # 兜底：PID 文件丢失 / 用户手动起过 logger 时会漏杀，残留会锁死 J-Link
    leftovers = [p for p in list_pids("JLinkRTTLogger.exe") if p not in pids]
    if not pids and not leftovers:
        print("⚠️ 没有正在运行的 RTT Logger")
        return 1

    stopped = [p for p in pids + leftovers if kill_pid(p)]
    try:
        RTT_PID_FILE.unlink()
    except OSError:
        pass
    if leftovers:
        print(f"🛑 RTT Logger 已停止 (PID {', '.join(map(str, stopped))})"
              f"，其中 {len(leftovers)} 个为按进程名兜底清理的残留")
    else:
        print(f"🛑 RTT Logger 已停止 (PID {', '.join(map(str, stopped))})")
    return 0

def _decode_log(raw: bytes, encoding: str) -> str:
    """解码 RTT 日志。`auto` = 先严格试 UTF-8，失败回落 GBK。

    Keil 工程的 RTT 输出通常是 GBK/ASCII，按 UTF-8 强解会把中文全变成 `�`；
    而现代工程/工具链输出多为 UTF-8，故先试 UTF-8 再回落，两边都不误伤。
    """
    if encoding and encoding != "auto":
        return raw.decode(encoding, errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="replace")

# 超过该大小的日志在 --tail 时只读尾部，避免整文件进内存
LOG_TAIL_READ_BYTES = 8 << 20

def rtt_show(log_file: str | Path, tail: int | None = None,
             encoding: str = "auto") -> list[str]:
    """读取 RTT 日志内容并返回行列表。"""
    log_path = Path(log_file)
    if not log_path.exists():
        print(f"❌ 日志文件不存在: {log_path}")
        return []
    try:
        size = log_path.stat().st_size
        with open(log_path, "rb") as fh:
            if tail is not None and size > LOG_TAIL_READ_BYTES:
                fh.seek(size - LOG_TAIL_READ_BYTES)
                raw = fh.read()
                # 从字节中途切入，首行多半残缺，丢弃
                raw = raw.split(b"\n", 1)[-1]
            else:
                raw = fh.read()
    except OSError as exc:
        print(f"❌ 读取失败: {exc}")
        return []
    lines = _decode_log(raw, encoding).splitlines()
    shown = lines if tail is None else lines[-tail:]
    for line in shown:
        print(line)
    return lines

# ---------------------------------------------------------------------------
# RTT 取证：直读 RAM 控制块 + 环形缓冲（savebin，非侵入）
# ---------------------------------------------------------------------------

# .map 中 RTT 控制块符号名（Keil/SEGGER 常见写法）
RTT_CB_SYMBOL_NAMES = ("_SEGGER_RTT", "SEGGER_RTT")

def resolve_rtt_cb(symbols: dict[str, dict[str, Any]]) -> tuple[int, str] | None:
    """从 .map 符号表定位 RTT 控制块地址，返回 (地址, 符号名)。"""
    for name in RTT_CB_SYMBOL_NAMES:
        if name in symbols:
            return symbols[name]["addr"], name
    low = {k.lower(): k for k in symbols}
    for name in RTT_CB_SYMBOL_NAMES:
        key = low.get(name.lower())
        if key:
            return symbols[key]["addr"], key
    return None

def extract_ring(buf: bytes, size: int, wr: int, rd: int) -> bytes:
    """从环形缓冲取 [rd, wr) 区间，处理回绕。"""
    if size <= 0 or len(buf) < size:
        return b""
    wr %= size
    rd %= size
    if wr == rd:
        return b""          # 空；或恰好写满一整圈（无法区分，见 --poll 丢行提示）
    if wr > rd:
        return buf[rd:wr]
    return buf[rd:] + buf[:wr]

def rtt_sample(
    jlink_exe: str,
    cb_addr: int,
    device: str,
    interface: str,
    speed: int,
    known: tuple[int, int] | None = None,
) -> dict[str, Any] | None:
    """一次 savebin 抓取 RTT 状态。

    `known=(pBuffer, SizeOfBuffer)` 时把控制块与缓冲**放在同一个脚本里一次读完**
    （每次采集只启动一次 JLink，约 1-2 秒）。缓冲若已被目标重配，本轮返回的
    `pbuf/size` 与 known 不符，调用方据此丢弃本轮并在下轮按新地址重取。
    """
    specs = [("cb.bin", cb_addr, RTT_CB_PROBE)]
    if known:
        specs.append(("up.bin", known[0], known[1]))
    got = _run_savebin(jlink_exe, specs, device, interface, speed)
    # 区分「本轮没请求缓冲」与「请求了但读失败」——前者应立刻重取，后者不该死循环
    requested = known is not None

    cb = got.get("cb.bin")
    if cb is None or cb[:len(RTT_CB_ID)] != RTT_CB_ID:
        return None                      # ID 校验：挡住 savebin 错位读出的垃圾
    num_up, num_down = struct.unpack_from("<II", cb, 16)
    if num_up < 1:
        return None                      # 未分配上行缓冲（RTT 未初始化）
    # SEGGER_RTT_CB: acID[16] + NumUp(4) + NumDown(4) = 24，aUp[0] 紧随其后
    # aUp[0]: sName*(4) pBuffer*(4) SizeOfBuffer(4) WrOff(4) RdOff(4) Flags(4)
    pbuf, size, wr, rd = struct.unpack_from("<IIII", cb, RTT_AUP_OFFSET + 4)
    if pbuf == 0 or not (0 < size <= (1 << 20)):
        return None                      # 指针/长度不合理 → 判为无效读
    return {"pbuf": pbuf, "size": size, "wr": wr, "rd": rd, "num_up": num_up,
            "num_down": num_down, "buf": got.get("up.bin"), "requested": requested}

def do_rtt_snapshot(args: Any, symbols: dict[str, dict[str, Any]]) -> int:
    """`--rtt snapshot`：直读 RAM 环形缓冲，取回**已发生**的 RTT 输出（含历史）。

    与 JLinkRTTLogger / GDBServer telnet 的区别：后两者只能拿 attach 之后的新数据，
    且高吞吐时会随机丢行；savebin 逐字节精确，是唯一可用于取证的方式。
    """
    jlink_exe, _ = find_jlink(args.jlink)
    if not jlink_exe:
        print("❌ 未找到 JLink.exe（请运行 /ea setup 注册，或用 --jlink 指定）")
        return 1

    cb_addr: int | None = None
    source = ""
    if args.cb_addr:
        try:
            cb_addr = int(args.cb_addr, 0)
            source = "--cb-addr"
        except ValueError:
            print(f"❌ --cb-addr 不是合法地址: {args.cb_addr}")
            return 1
    else:
        found = resolve_rtt_cb(symbols)
        if found:
            cb_addr, name = found
            source = f".map 符号 {name}"
    if cb_addr is None:
        print("❌ 未定位到 RTT 控制块地址")
        print("   对策 ① 确认 .map 含 _SEGGER_RTT：--list-symbols _SEGGER_RTT")
        print("        ② 显式指定：--cb-addr 0x200014DC")
        print("        ③ .map 不在 cwd：--map <路径>")
        return 1
    print(f"🎯 RTT 控制块 0x{cb_addr:08X}（来源：{source}）")

    if args.dry_run:
        print("(dry-run) savebin 采集计划：")
        print(f"  1) savebin cb.bin 0x{cb_addr:08X} 0x{RTT_CB_PROBE:X}"
              f"   → 校验 cb[0:10]=='SEGGER RTT'")
        print("  2) 取 aUp[0] 的 pBuffer / SizeOfBuffer / WrOff / RdOff")
        print("  3) savebin up.bin <pBuffer> <SizeOfBuffer>  → 按 WrOff 取 [rd, wr)")
        print(f"  JLink: -Device {args.device} -If {args.interface} -Speed {args.speed} "
              f"-AutoConnect 1 -ExitOnError 1   （connect 不复位、不 halt）")
        return 0

    if args.reset_run:
        if do_reset_run(jlink_exe, args.device, args.interface, args.speed, False) != 0:
            print("❌ 复位+放行失败，中止采集")
            return 1

    enc = args.encoding
    duration = args.duration
    # 每次采集要启一个 JLink 进程（约 1-2s），故轮询间隔默认比 --mem 的 0.3s 长
    interval = args.interval if args.interval is not None else 5.0
    poll_max = args.poll if (args.poll and args.poll > 0) else None
    if duration is None and poll_max is None:
        poll_max = 1                       # 缺省单次快照

    print(f"📡 采集方式: savebin 直读 RAM（不 halt、不复位，对目标零干扰）")
    if poll_max == 1:
        print("   单次快照 → 取回环形缓冲中全部未读数据")
    else:
        print(f"   轮询：间隔 {interval}s"
              + (f"，持续 {duration}s" if duration else f"，共 {poll_max} 次"))

    known: tuple[int, int] | None = None
    last_wr: int | None = None
    chunks: list[bytes] = []
    lost_bytes = 0
    iteration = 0
    deadline = (time.time() + duration) if duration else None

    while True:
        iteration += 1
        s = rtt_sample(jlink_exe, cb_addr, args.device, args.interface,
                       args.speed, known)
        if s is None:
            print(f"   [{iteration}] ⚠️ 读取无效（连接失败 / 控制块校验不过），跳过本轮")
        elif not s["requested"]:
            # 尚未知道缓冲地址：本轮只拿到控制块，登记后立即重取（不占用采样次数）
            known = (s["pbuf"], s["size"])
            print(f"   [{iteration}] ℹ️ 上行缓冲 0x{s['pbuf']:08X} "
                  f"size={s['size']} wr={s['wr']} rd={s['rd']}")
            continue
        elif s["buf"] is None:
            print(f"   [{iteration}] ⚠️ 上行缓冲读取失败（savebin 未返回，地址失效？）")
            known = None                     # 下轮重新走一次地址定位
        elif (s["pbuf"], s["size"]) != known:
            print(f"   [{iteration}] ℹ️ 上行缓冲已被重配 → 0x{s['pbuf']:08X}，下轮重取")
            known = None
        else:
            size, wr, buf = s["size"], s["wr"], s["buf"]
            if last_wr is None:
                new = extract_ring(buf, size, wr, s["rd"])       # 首轮：取全部可用历史
                tag = f"历史 {len(new)}B"
            else:
                delta = (wr - last_wr) % size
                if delta == 0:
                    new, tag = b"", "无新增"
                elif wr > last_wr:
                    new = buf[last_wr:wr]
                    tag = f"+{len(new)}B"
                else:                                             # 环形回绕
                    new = buf[last_wr:] + buf[:wr]
                    tag = f"+{len(new)}B(回绕)"
                if delta > size * 0.9:
                    lost_bytes += delta
                    print(f"   [{iteration}] ⚠️ 两次采样间近似写满整圈"
                          f"({delta}/{size}B) → 可能有丢行，缩短 --interval")
            last_wr = wr
            if new:
                chunks.append(new)
                print(f"   [{iteration}] wr={wr}  {tag}")
                if not args.json:
                    print("      | " + _decode_log(new, enc).rstrip()
                          .replace("\n", "\n      | "))

        if deadline is not None:
            if time.time() >= deadline:
                break
            time.sleep(interval)
        else:
            poll_max -= 1
            if poll_max <= 0:
                break
            time.sleep(interval)

    data = b"".join(chunks)
    text = _decode_log(data, enc)
    print("─" * 60)
    print(f"✅ RTT 快照完成：{len(data)} 字节 / {len(text.splitlines())} 行"
          + (f"（⚠️ 疑似丢失 {lost_bytes} 字节）" if lost_bytes else ""))
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        print(f"   已写入 {out_path}")
    if args.json:
        print(json.dumps({"cb_addr": f"0x{cb_addr:08X}", "bytes": len(data),
                          "lines": len(text.splitlines()), "lost": lost_bytes,
                          "encoding": enc, "text": text}, ensure_ascii=False))
    return 0

# ---------------------------------------------------------------------------
# 断点 / 内存 / 寄存器（Commander 脚本通道）
# ---------------------------------------------------------------------------

def do_bp(
    symbol_or_addr: str,
    symbols: dict[str, dict[str, Any]],
    jlink_exe: str,
    run_ms: int,
    device: str,
    dry_run: bool,
) -> int:
    addr = parse_address(symbol_or_addr, symbols)
    if addr is None:
        print(f"❌ 无法解析断点目标: {symbol_or_addr}（应为 0x 地址或 .map 中符号名）")
        matches = resolve_symbol(symbol_or_addr, symbols)
        if matches:
            print("   相似符号：")
            for k, v in matches[:8]:
                print(f"     {k}  {v['value']}  {v['type']}  size={v['size']}")
        return 1

    matches = resolve_symbol(symbol_or_addr, symbols) if not symbol_or_addr.lower().startswith("0x") else []
    label = f"{symbol_or_addr} @ 0x{addr:08X}"
    if matches:
        label = f"{matches[0][0]} @ 0x{addr:08X} (0x{addr:08X})"

    script = build_commander_script(device, DEFAULT_INTERFACE, DEFAULT_SPEED, [
        f"SetBP 0x{addr:08X}",
        "go",
        f"Sleep {run_ms}",
        "halt",
        "regs",
    ])
    print(f"🎯 断点: {label}")
    print(f"   SetBP → go → Sleep {run_ms}ms → halt → regs")

    if dry_run:
        print("\n--- 生成的 JLink 脚本 (dry-run) ---")
        print(script)
        print("------------------------------------")
        return 0

    try:
        result = run_commander(jlink_exe, script, timeout=run_ms // 1000 + 30,
                               device=device)
    except subprocess.TimeoutExpired:
        print("❌ JLink Commander 执行超时")
        return 1

    output = result.stdout
    if result.returncode != 0 or "Cannot connect" in output or "Could not connect" in output:
        print("❌ 连接失败")
        diagnose_connect_failure(output)
        return 1

    regs = parse_regs(output)
    pc = regs.get("R15") or regs.get("PC")
    print("\n📊 停止位置分析：")
    if pc is not None:
        hit_name = "?"
        for name, info in symbols.items():
            if info["addr"] == (pc & ~1) or info["addr"] <= (pc & ~1) < info["addr"] + max(info["size"], 1):
                if info["type"] in ("Thumb Code", "Code"):
                    hit_name = name
                    break
        print(f"   PC = 0x{pc:08X}  ({hit_name})")
        for name, info in symbols.items():
            if info["addr"] == (pc & ~1) and info["type"] in ("Thumb Code", "Code"):
                print(f"   → 命中断点: {name}  size={info['size']}")
                break
    for name in ("R0", "R1", "R2", "R3", "R14", "SP"):
        if name in regs:
            print(f"   {name} = 0x{regs[name]:08X}")
    return 0

def do_mem(args: Any, symbols: dict[str, dict[str, Any]], jlink_exe: str,
           ocd_port: int) -> int:
    """读内存变量。

    jlink 后端默认 `halt → 读 → go`，会**短暂打断**目标；`--no-halt` 改走
    `savebin` 经 AHB-AP 后台读，完全不 halt —— 用于「目标正在跑人工交互测试、
    我要在过程中反复采样」的场景。OpenOCD 后端本就免停机。
    """
    symbol_or_addr, backend = args.mem, args.backend
    count, width = args.count, args.width
    watch = args.watch
    interval = args.interval if args.interval is not None else 0.3
    dry_run, json_out, no_halt = args.dry_run, args.json, args.no_halt

    addr = parse_address(symbol_or_addr, symbols)
    if addr is None:
        print(f"❌ 无法解析内存目标: {symbol_or_addr}")
        return 1

    # --width 的取值即「每元素字节数」：4=32bit、1=8bit
    unit = width
    n_bytes = count * unit
    cmd = "mem32" if unit == 4 else "mem8"
    # OpenOCD 的 mdw/mdh/mdb 按**位**选命令，必须换算，否则查表落空读不到数据
    ocd_bits = unit * 8
    matches = resolve_symbol(symbol_or_addr, symbols) if not symbol_or_addr.lower().startswith("0x") else []
    label = f"{symbol_or_addr} @ 0x{addr:08X}" if not matches else f"{matches[0][0]} @ 0x{addr:08X}"
    if matches:
        info = matches[0][1]
        label += f"  [{info['type']} size={info['size']}]"

    # 免停机路径：openocd 的 TCL mdw 本就直读；jlink 靠 --no-halt 切到 savebin
    halt_free = backend == "openocd" or no_halt

    def _one_sample() -> dict[int, int]:
        if backend == "openocd":
            vals = ocd_read_mem(ocd_port, addr, count, ocd_bits)
            return vals if vals is not None else {}
        if halt_free:
            raw = savebin_read(jlink_exe, addr, n_bytes,
                               args.device, args.interface, args.speed)
            if raw is None:
                return {}
            if unit == 4:
                return {addr + i * 4: int.from_bytes(raw[i * 4:i * 4 + 4], "little")
                        for i in range(len(raw) // 4)}
            return {addr + i: b for i, b in enumerate(raw)}
        script = build_commander_script(args.device, args.interface, args.speed, [
            "halt",
            f"{cmd} 0x{addr:08X} {count}",
            "go",
        ])
        if dry_run:
            print(f"\n--- 生成的 JLink 脚本 (dry-run) ---\n{script}---")
            return {}
        res = run_commander(jlink_exe, script, device=args.device,
                            interface=args.interface, speed=args.speed)
        if unit == 4:
            return parse_mem32(res.stdout)
        return parse_mem8(res.stdout)

    way = "savebin 后台读（不 halt）" if halt_free else "halt → 读 → go"
    if dry_run:
        if backend == "openocd":
            print(f"(dry-run) OpenOCD 后端: TCL {cmd} 0x{addr:08X} {count}（无停机，不 halt）")
        elif no_halt:
            print(f"(dry-run) JLink 后端 --no-halt: savebin 0x{addr:08X} "
                  f"0x{n_bytes:X}（AHB-AP 后台读，不 halt 目标）")
        else:
            print(f"(dry-run) JLink 后端: halt → {cmd} → go")
        return 0

    samples: list[dict[int, int]] = []
    for i in range(max(1, watch)):
        values = _one_sample()
        if not values:
            print(f"❌ 第 {i + 1} 次读取无返回")
            return 1
        samples.append(values)
        if watch == 1:
            print(f"📍 内存 {label}")
            print(f"   读取 {count} × {cmd}（{way}）")
            for base in sorted(values):
                if unit == 4:
                    print(f"     0x{base:08X} = 0x{values[base]:08X}")
                else:
                    print(f"     0x{base:08X} = 0x{values[base]:02X}")
        else:
            first = samples[0]
            line = f"   t={i}: "
            for base in sorted(first):
                val = values.get(base)
                fmt = "08X" if unit == 4 else "02X"
                line += f"0x{base:08X}=0x{val:{fmt}}  "
            print(line)
        if watch > 1:
            time.sleep(interval)

    if json_out:
        payload: dict[str, Any] = {"backend": backend, "symbol": label, "addr": f"0x{addr:08X}",
                                   "cmd": cmd, "no_halt": halt_free, "samples": samples}
        print(json.dumps(payload, ensure_ascii=False))
    return 0

def do_reset_run(jlink_exe: str, device: str, interface: str, speed: int,
                 dry_run: bool = False) -> int:
    """`--reset-run`：复位并放行目标（`r` + `g`），之后让它自由运行。

    `--bp`（跑到断点）和 `--mem`（halt→读→go）都不含「从复位开始」，而配合人工
    交互测试时需要的恰恰是「从干净状态复位起跑，然后我不干预」。
    """
    script = "\n".join(["r", "g", "qc"]) + "\n"
    if dry_run:
        print("(dry-run) JLink 脚本: r（复位）→ g（放行）→ qc")
        print(f"   命令行: -Device {device} -If {interface} -Speed {speed} "
              f"-AutoConnect 1 -ExitOnError 1")
        return 0
    print(f"🔄 复位并放行目标（{device} {interface}@{speed}）")
    try:
        res = run_commander(jlink_exe, script, timeout=30,
                            device=device, interface=interface, speed=speed)
    except subprocess.TimeoutExpired:
        print("❌ JLink Commander 执行超时")
        return 1
    if res.returncode != 0 or "Cannot connect" in res.stdout:
        print("❌ 复位+放行失败")
        diagnose_connect_failure(res.stdout)
        return 1
    print("✅ 目标已复位并放行（现在自由运行，savebin/内存读取均不打断它）")
    return 0

def do_regs(args: Any, jlink_exe: str, ocd_port: int) -> int:
    backend, dry_run, json_out = args.backend, args.dry_run, args.json
    if backend == "openocd":
        if dry_run:
            print("(dry-run) OpenOCD 后端: TCL `reg` 读寄存器")
            return 0
        regs = ocd_read_regs(ocd_port)
        if not regs:
            print("❌ 无法读取寄存器（OpenOCD TCL 无响应，连接失败？）")
            return 1
        if json_out:
            print(json.dumps(regs, ensure_ascii=False))
            return 0
        print("📋 寄存器（OpenOCD）：")
        for name in ("R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11", "R12", "R13", "SP", "R14", "LR", "R15", "PC", "xPSR"):
            if name in regs:
                print(f"   {name:>4} = 0x{regs[name]:08X}")
        return 0

    script = build_commander_script(args.device, args.interface, args.speed,
                                    ["halt", "regs", "go"])
    if dry_run:
        print(f"--- 生成的 JLink 脚本 (dry-run) ---\n{script}---")
        return 0
    try:
        result = run_commander(jlink_exe, script, device=args.device,
                               interface=args.interface, speed=args.speed)
    except subprocess.TimeoutExpired:
        print("❌ JLink Commander 执行超时")
        return 1
    regs = parse_regs(result.stdout)
    if not regs:
        print("❌ 无法读取寄存器")
        diagnose_connect_failure(result.stdout)
        return 1
    if json_out:
        print(json.dumps(regs, ensure_ascii=False))
        return 0
    print("📋 寄存器：")
    for name in ("R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11", "R12", "R13", "SP", "R14", "LR", "R15", "PC", "xPSR"):
        if name in regs:
            print(f"   {name:>4} = 0x{regs[name]:08X}")
    return 0

# ---------------------------------------------------------------------------
# gdb 源码级调试（可选增强）
# ---------------------------------------------------------------------------

def do_gdb(
    gdb_script: str | None,
    elf: str | None,
    gdbserver_port: int,
    dry_run: bool,
) -> int:
    peers = find_peers()
    if not peers["gdb"]:
        print("❌ 未找到 arm-none-eabi-gdb，源码级调试不可用。")
        print("   ℹ️ 当前用 JLink Commander 脚本方案（--bp/--mem/--regs），零依赖可用。")
        print("   如需源码级调试，安装 GNU Arm 工具链：")
        print("     https://developer.arm.com/downloads/-/gnu-rm")
        print("   安装后请确认 arm-none-eabi-gdb 在 PATH 中。")
        return 1
    if not peers["gdb_server"]:
        print("❌ 未找到 JLinkGDBServerCL.exe，无法启动 GDB Server。")
        return 1

    print(f"✅ gdb: {peers['gdb']}")
    print(f"   GDB Server: {peers['gdb_server']}")
    print(f"   设备: {DEFAULT_DEVICE} | 端口: {gdbserver_port}")

    if dry_run:
        print("   (dry-run: 不会真正启动 GDBServer)")
        return 0

    gdb_server_proc = subprocess.Popen(
        [peers["gdb_server"],
         "-device", DEFAULT_DEVICE,
         "-if", DEFAULT_INTERFACE,
         "-speed", str(DEFAULT_SPEED),
         "-port", str(gdbserver_port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(1.5)
        cmd = [peers["gdb"], "-batch"]
        if elf:
            # Windows 反斜杠路径在 gdb 里会被吞掉（\w → w），必须转正斜杠；
            # 先 file 加载符号再连 target，避免 "No executable" 警告
            cmd += ["-ex", f"file {os.path.normpath(elf).replace(chr(92), '/')}"]
        cmd += ["-ex", f"target remote :{gdbserver_port}"]
        if gdb_script:
            cmd += ["-x", gdb_script]
        else:
            cmd += [
                "-ex", "info registers",
                "-ex", "monitor reset halt",
            ]
        print("▶️  启动 gdb -batch ...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        print(result.stdout)
        if result.stderr.strip():
            print("(stderr) ", result.stderr.strip())
        return 0 if result.returncode == 0 else 1
    finally:
        gdb_server_proc.terminate()

# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def print_detect_report() -> int:
    peers = find_peers()
    print("🔧 J-Link 调试工具链探测：")
    rows = [
        ("JLink.exe", peers["jlink"]),
        ("JLinkRTTLogger.exe", peers["rtt_logger"]),
        ("JLinkGDBServerCL.exe", peers["gdb_server"]),
        ("arm-none-eabi-gdb", peers["gdb"]),
    ]
    found = 0
    for label, path in rows:
        if path:
            print(f"  ✅ {label:<22} {path}")
            found += 1
        else:
            print(f"  ⬜ {label:<22} 未找到")
    print(f"\n结果: {found}/4 工具可用")
    if not peers["jlink"]:
        print("   ❌ JLink.exe 未找到：请运行 /ea setup 注册，或用 --jlink 指定路径")
        return 1
    return 0

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jlink_debug.py",
        description="J-Link 调试工具（RTT 日志 / 断点定位 / 内存监视 / 寄存器 / gdb）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --detect
  %(prog)s --rtt start --log .ea/logs/rtt.log      # 只抓新数据，须先 start 再复位
  %(prog)s --rtt stop
  %(prog)s --rtt show --log .ea/logs/rtt.log --tail 20
  %(prog)s --rtt snapshot                          # 直读 RAM，取回已发生的历史日志
  %(prog)s --rtt snapshot --poll 40 --interval 5 --out .ea/logs/rtt.log
  %(prog)s --reset-run                             # 复位+放行，让目标自由跑
  %(prog)s --rtt snapshot --reset-run --duration 420 --interval 5
  %(prog)s --bp Display_BootDoraemon --run-ms 2000
  %(prog)s --bp 0x08001d28
  %(prog)s --mem _TimeCount_10ms --watch 3 --count 4
  %(prog)s --mem _TimeCount_10ms --no-halt --watch 10   # 不打断正在跑的目标
  %(prog)s --regs --json
  %(prog)s --gdb --elf build/app.axf --gdb-script debug.gdb
        """,
    )
    # 动作（互斥组）
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--detect", action="store_true", help="探测 J-Link 工具链")
    actions.add_argument("--rtt", choices=["start", "stop", "show", "snapshot"],
                         help="RTT 日志：start/stop/show=JLinkRTTLogger（只抓新数据）；"
                              "snapshot=savebin 直读 RAM（含历史，可取证）")
    actions.add_argument("--bp", metavar="<函数名/地址>",
                         help="设断点 + 运行 → halt → 报告 PC（看代码跑哪了）")
    actions.add_argument("--mem", metavar="<变量名/地址>",
                         help="读取内存变量（默认 mem32）")
    actions.add_argument("--regs", action="store_true", help="读取 CPU 寄存器")
    actions.add_argument("--gdb", action="store_true",
                         help="源码级调试（需 arm-none-eabi-gdb + JLinkGDBServerCL）")

    # 参数
    parser.add_argument("--map", help="指定 .map 符号文件（默认自动扫描 cwd）")
    parser.add_argument("--list-symbols", metavar="<过滤>", nargs="?",
                        const="", help="列出 .map 中符号（可传过滤子串）")
    parser.add_argument("--resolve", metavar="<名字>",
                        help="解析符号名 → 地址（用于核对映射）")
    parser.add_argument("--jlink", help="显式指定 JLink.exe 路径")
    parser.add_argument("--backend", choices=["jlink", "openocd"], default="jlink",
                        help="调试后端：jlink（默认）/ openocd（ST-Link/DAP，无停机监控）")
    parser.add_argument("--ocd-if", choices=["stlink", "cmsis-dap", "jlink"],
                        help="OpenOCD 接口（缺省自动探测）")
    parser.add_argument("--ocd-target", default="target/stm32f4x.cfg",
                        help="OpenOCD 目标配置（默认 target/stm32f4x.cfg，Cortex-M4 通用）")
    parser.add_argument("--ocd-port", type=int, default=DEFAULT_OCD_PORT,
                        help=f"OpenOCD TCL 端口（默认 {DEFAULT_OCD_PORT}）")
    parser.add_argument("--interval", type=float, default=None,
                        help="采样间隔秒（--mem --watch 默认 0.3；--rtt snapshot 默认 5.0）")
    parser.add_argument("--no-halt", action="store_true",
                        help="--mem 用 savebin 后台读，不 halt CPU（目标全速运行时采样）")
    parser.add_argument("--reset-run", action="store_true",
                        help="复位并放行目标（r+g）。单独使用，或配合 --rtt snapshot 先复位")
    parser.add_argument("--cb-addr", metavar="<地址>",
                        help="--rtt snapshot 的 RTT 控制块地址（缺省从 .map 的 _SEGGER_RTT 解析）")
    parser.add_argument("--poll", type=int, metavar="N",
                        help="--rtt snapshot 轮询 N 次（缺省 1 次快照；每次只取新增段）")
    parser.add_argument("--duration", type=float, metavar="S",
                        help="--rtt snapshot 持续采集 S 秒（与 --poll 二选一，优先本项）")
    parser.add_argument("--encoding", default="auto",
                        help="RTT 日志解码（默认 auto=先试 UTF-8 再回落 GBK；可指定 gbk/utf-8）")
    parser.add_argument("--out", metavar="<文件>", help="--rtt snapshot 把原始字节写入文件")
    parser.add_argument("--force", action="store_true",
                        help="--rtt start 时强制清理已占用的 JLinkRTTLogger")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help=f"芯片型号（默认 {DEFAULT_DEVICE}）")
    parser.add_argument("--if", dest="interface", default=DEFAULT_INTERFACE, help=f"接口（默认 {DEFAULT_INTERFACE}）")
    parser.add_argument("--speed", type=int, default=DEFAULT_SPEED, help=f"SWD 速度 kHz（默认 {DEFAULT_SPEED}）")
    parser.add_argument("--run-ms", type=int, default=DEFAULT_RUN_MS,
                        help=f"--bp 后运行毫秒数（默认 {DEFAULT_RUN_MS}，注意 IWDG 看门狗）")
    parser.add_argument("--count", type=int, default=4, help="--mem 读取的字数/字节数")
    parser.add_argument("--width", type=int, choices=[1, 4], default=4, help="--mem 宽度：4=32位 1=8位")
    parser.add_argument("--watch", type=int, default=1, help="--mem 采样次数（>1 持续监视）")
    parser.add_argument("--log", default="rtt.log", help="--rtt 的日志文件路径")
    parser.add_argument("--tail", type=int, help="--rtt show 只显示末尾 N 行")
    parser.add_argument("--channel", type=int, default=DEFAULT_RTT_CHANNEL,
                        help=f"RTT 通道（默认 {DEFAULT_RTT_CHANNEL}）")
    parser.add_argument("--gdb-script", help="--gdb 时指定 gdb 命令脚本")
    parser.add_argument("--elf", help="--gdb 时指定 ELF/AXF 文件（加载符号）")
    parser.add_argument("--gdb-port", type=int, default=2331, help="--gdb 的 GDB Server 端口")
    parser.add_argument("--dry-run", action="store_true", help="只打印生成的脚本，不执行")
    parser.add_argument("--json", action="store_true", help="结构化 JSON 输出（供 AI 解析）")
    return parser

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.detect:
        return print_detect_report()

    if args.no_halt and not args.mem:
        print("⚠️ --no-halt 只对 --mem 有效，已忽略")

    # 符号表加载（bp / mem / list-symbols / resolve / rtt snapshot 需要）
    symbols: dict[str, dict[str, Any]] = {}
    if args.map:
        symbols = parse_map_symbols(args.map)
        if not symbols:
            print(f"❌ 无法解析 .map: {args.map}（或无有效符号）")
            return 1
    elif (args.bp or args.mem or args.list_symbols is not None
          or args.resolve or args.regs or args.rtt == "snapshot"):
        map_path = find_map_file()
        if map_path:
            symbols = parse_map_symbols(map_path)
            print(f"ℹ️ 自动发现符号文件: {map_path}（{len(symbols)} 个符号）")

    # 后端校验与工具链
    if args.backend == "openocd":
        if args.rtt or args.bp or args.gdb:
            print("❌ OpenOCD 后端仅支持 --mem/--regs（--rtt/--bp/--gdb 需 --backend jlink）")
            return 1
        if not _get_openocd_exe():
            print("❌ 未找到 openocd（请运行 /ea setup 注册，或加入 PATH）")
            return 1
    jlink_exe, _ = find_jlink(args.jlink)
    if args.backend == "jlink" and (args.bp or args.mem or args.regs) and not jlink_exe:
        print("❌ 未找到 JLink.exe（请运行 /ea setup 注册，或用 --jlink 指定）")
        return 1

    if args.list_symbols is not None:
        if not symbols:
            print("❌ 未加载任何符号（需 --map 或在工程目录运行）")
            return 1
        matches = resolve_symbol(args.list_symbols, symbols) if args.list_symbols else sorted(symbols.items())
        print(f"📖 符号表（{len(matches)} 个，filter='{args.list_symbols or '*'}'）：")
        for name, info in matches[:80]:
            print(f"   {name:<40} {info['value']}  {info['type']:<14} size={info['size']}")
        if len(matches) > 80:
            print(f"   ... 共 {len(matches)} 个")
        return 0

    if args.resolve:
        if not symbols:
            print("❌ 未加载任何符号")
            return 1
        matches = resolve_symbol(args.resolve, symbols)
        if not matches:
            print(f"❌ 符号未找到: {args.resolve}")
            return 1
        for name, info in matches[:10]:
            print(f"{name:<40} {info['value']}  {info['type']:<14} size={info['size']}  {info['object']}")
        return 0

    if args.rtt == "start":
        return rtt_start(args.device, args.interface, args.speed, args.channel,
                         args.log, force=args.force)
    if args.rtt == "stop":
        return rtt_stop()
    if args.rtt == "show":
        rtt_show(args.log, args.tail, args.encoding)
        return 0
    if args.rtt == "snapshot":
        return do_rtt_snapshot(args, symbols)

    if args.reset_run and (args.bp or args.mem or args.regs or args.gdb):
        print("⚠️ --reset-run 仅支持单独使用或配合 --rtt snapshot，本次已忽略")

    # --reset-run 单独使用时就是一个纯动作（复位+放行）
    if args.reset_run and not (args.bp or args.mem or args.regs or args.gdb):
        jlink_exe, _ = find_jlink(args.jlink)
        if not jlink_exe:
            print("❌ 未找到 JLink.exe（请运行 /ea setup 注册，或用 --jlink 指定）")
            return 1
        return do_reset_run(jlink_exe, args.device, args.interface, args.speed,
                            args.dry_run)

    # OpenOCD 后端：确保 TCL 服务在跑（--mem/--regs 需要；复用时不动用户进程）
    ocd_proc = None
    if args.backend == "openocd" and not args.dry_run:
        ocd_proc, _reused = ocd_start(args.ocd_if, args.ocd_target, args.ocd_port)
        if ocd_proc is None and not _reused:
            return 1

    try:
        if args.bp:
            return do_bp(args.bp, symbols, jlink_exe, args.run_ms, args.device, args.dry_run)
        if args.mem:
            return do_mem(args, symbols, jlink_exe, args.ocd_port)
        if args.regs:
            return do_regs(args, jlink_exe, args.ocd_port)
        if args.gdb:
            return do_gdb(args.gdb_script, args.elf, args.gdb_port, args.dry_run)
    finally:
        ocd_stop(ocd_proc)

    parser.print_help()
    return 0

if __name__ == "__main__":
    sys.exit(main())
