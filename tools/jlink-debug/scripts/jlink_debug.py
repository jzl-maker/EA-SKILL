#!/usr/bin/env python
"""通用嵌入式调试工具（RTT 日志 + 断点定位 + 内存监视 + 寄存器）。

为 `jlink-debug` skill 提供可重复调用的执行入口，支持：

- `--rtt`         RTT 日志抓取（JLinkRTTLogger，channel 0）
- `--bp`          断点定位「代码跑哪了」（.map 符号 → JLink Commander SetBP → go → halt → PC/regs）
- `--mem`         监视内存变量（mem32/mem8，按符号名或地址；--monitor 无停机采样）
- `--regs`        读 CPU 寄存器（PC/SP/LR/…）
- `--gdb`         源码级调试（可选：检测 arm-none-eabi-gdb + JLinkGDBServerCL）

双后端：
- `--backend jlink`（默认）：J-Link Commander 脚本（JLinkRTTLogger/SetBP/halt+mem）
- `--backend openocd`：OpenOCD TCL 端口（ST-Link/CMSIS-DAP/J-Link 通用）。
  --mem/--regs 走 TCL mdw/reg 命令，**运行中读取不 halt CPU**（无停机实时监控）。
  --bp/--rtt/--gdb 在 OpenOCD 后端暂不支持（用 --backend jlink）。

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
) -> subprocess.CompletedProcess[str]:
    """Execute JLink Commander script file.

    JLink older versions (< V7.60) do NOT support `-CommanderScript -` (stdin);
    must write a temp .jlink script file and pass its path.
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".jlink", prefix="ea_skill_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        return subprocess.run(
            [jlink_exe, "-CommanderScript", tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

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
# RTT 日志抓取
# ---------------------------------------------------------------------------

def rtt_start(device: str, interface: str, speed: int, channel: int, log_file: str | Path) -> int:
    """后台启动 JLinkRTTLogger，返回 PID。"""
    peers = find_peers()
    if not peers["rtt_logger"]:
        print("❌ 未找到 JLinkRTTLogger.exe（J-Link 安装目录）")
        return 1

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
    print("   设备运行时请触发复位，日志将写入文件。")
    return 0

def rtt_stop() -> int:
    """停止已启动的 JLinkRTTLogger。"""
    if not RTT_PID_FILE.exists():
        print("⚠️ 没有正在运行的 RTT Logger（未记录 PID）")
        return 1
    pid = int(RTT_PID_FILE.read_text(encoding="utf-8").strip())
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, text=True, timeout=10)
        print(f"🛑 RTT Logger 已停止 (PID {pid})")
    except Exception as exc:  # pragma: no cover
        print(f"⚠️ 停止失败: {exc}")
        return 1
    finally:
        try:
            RTT_PID_FILE.unlink()
        except OSError:
            pass
    return 0

def rtt_show(log_file: str | Path, tail: int | None = None) -> list[str]:
    """读取 RTT 日志内容并返回行列表。"""
    log_path = Path(log_file)
    if not log_path.exists():
        print(f"❌ 日志文件不存在: {log_path}")
        return []
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    shown = lines if tail is None else lines[-tail:]
    for line in shown:
        print(line)
    return lines

# ---------------------------------------------------------------------------
# 断点 / 内存 / 寄存器（Commander 脚本通道）
# ---------------------------------------------------------------------------

def halt_then(script_body: list[str], jlink_exe: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    body = ["halt", *script_body]
    full = build_commander_script(DEFAULT_DEVICE, DEFAULT_INTERFACE, DEFAULT_SPEED, body)
    return run_commander(jlink_exe, full, timeout)

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
        result = run_commander(jlink_exe, script, timeout=run_ms // 1000 + 30)
    except subprocess.TimeoutExpired:
        print("❌ JLink Commander 执行超时")
        return 1

    output = result.stdout
    if result.returncode != 0 or "Cannot connect" in output or "Could not connect" in output:
        print("❌ 连接失败，输出：")
        print(output[-800:])
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

def do_mem(
    symbol_or_addr: str,
    symbols: dict[str, dict[str, Any]],
    backend: str,
    jlink_exe: str,
    ocd_port: int,
    count: int,
    width: int,
    watch: int,
    interval: float,
    dry_run: bool,
    json_out: bool,
) -> int:
    addr = parse_address(symbol_or_addr, symbols)
    if addr is None:
        print(f"❌ 无法解析内存目标: {symbol_or_addr}")
        return 1

    unit = width  # 4 => mem32, 1 => mem8
    cmd = "mem32" if unit == 4 else "mem8"
    matches = resolve_symbol(symbol_or_addr, symbols) if not symbol_or_addr.lower().startswith("0x") else []
    label = f"{symbol_or_addr} @ 0x{addr:08X}" if not matches else f"{matches[0][0]} @ 0x{addr:08X}"
    if matches:
        info = matches[0][1]
        label += f"  [{info['type']} size={info['size']}]"

    def _one_sample() -> dict[int, int]:
        if backend == "openocd":
            # 无停机：TCL mdw/mdb 运行中直接读，不 halt
            vals = ocd_read_mem(ocd_port, addr, count, unit)
            if vals is None:
                return {}
            return vals
        script = build_commander_script(DEFAULT_DEVICE, DEFAULT_INTERFACE, DEFAULT_SPEED, [
            "halt",
            f"{cmd} 0x{addr:08X} {count}",
            "go",
        ])
        if dry_run:
            print(f"\n--- 生成的 JLink 脚本 (dry-run) ---\n{script}---")
            return {}
        res = run_commander(jlink_exe, script)
        if unit == 4:
            return parse_mem32(res.stdout)
        return parse_mem8(res.stdout)

    if dry_run:
        if backend == "openocd":
            print(f"(dry-run) OpenOCD 后端: TCL {cmd} 0x{addr:08X} {count}（无停机，不 halt）")
        else:
            print(f"(dry-run) JLink 后端: halt → {cmd} → go")
        return 0

    samples: list[dict[int, int]] = []
    for i in range(max(1, watch)):
        values = _one_sample()
        if not values:
            print(f"❌ 第 {i + 1} 次读取无返回（连接失败？）")
            return 1
        samples.append(values)
        if watch == 1:
            print(f"📍 内存 {label}")
            print(f"   读取 {count} × {cmd}" + ("（无停机）" if backend == "openocd" else ""))
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
                                   "cmd": cmd, "samples": samples}
        print(json.dumps(payload, ensure_ascii=False))
    return 0

def do_regs(backend: str, jlink_exe: str, ocd_port: int, dry_run: bool, json_out: bool) -> int:
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

    script = build_commander_script(DEFAULT_DEVICE, DEFAULT_INTERFACE, DEFAULT_SPEED,
                                    ["halt", "regs", "go"])
    if dry_run:
        print(f"--- 生成的 JLink 脚本 (dry-run) ---\n{script}---")
        return 0
    try:
        result = run_commander(jlink_exe, script)
    except subprocess.TimeoutExpired:
        print("❌ JLink Commander 执行超时")
        return 1
    regs = parse_regs(result.stdout)
    if not regs:
        print("❌ 无法读取寄存器（连接失败？）")
        print(result.stdout[-600:])
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
  %(prog)s --rtt start --log .em/logs/rtt.log
  %(prog)s --rtt stop
  %(prog)s --rtt show --log .em/logs/rtt.log --tail 20
  %(prog)s --bp Display_BootDoraemon --run-ms 2000
  %(prog)s --bp 0x08001d28
  %(prog)s --mem _TimeCount_10ms --watch 3 --count 4
  %(prog)s --regs --json
  %(prog)s --gdb --elf build/app.axf --gdb-script debug.gdb
        """,
    )
    # 动作（互斥组）
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--detect", action="store_true", help="探测 J-Link 工具链")
    actions.add_argument("--rtt", choices=["start", "stop", "show"],
                         help="RTT 日志抓取管理")
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
    parser.add_argument("--interval", type=float, default=0.3,
                        help="--mem --watch N 的采样间隔秒（默认 0.3）")
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

    # 符号表加载（bp / mem / list-symbols / resolve 需要）
    symbols: dict[str, dict[str, Any]] = {}
    if args.map:
        symbols = parse_map_symbols(args.map)
        if not symbols:
            print(f"❌ 无法解析 .map: {args.map}（或无有效符号）")
            return 1
    elif args.bp or args.mem or args.list_symbols is not None or args.resolve or args.regs:
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
        return rtt_start(args.device, args.interface, args.speed, args.channel, args.log)
    if args.rtt == "stop":
        return rtt_stop()
    if args.rtt == "show":
        rtt_show(args.log, args.tail)
        return 0

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
            return do_mem(args.mem, symbols, args.backend, jlink_exe, args.ocd_port,
                          args.count, args.width, args.watch, args.interval,
                          args.dry_run, args.json)
        if args.regs:
            return do_regs(args.backend, jlink_exe, args.ocd_port, args.dry_run, args.json)
        if args.gdb:
            return do_gdb(args.gdb_script, args.elf, args.gdb_port, args.dry_run)
    finally:
        ocd_stop(ocd_proc)

    parser.print_help()
    return 0

if __name__ == "__main__":
    sys.exit(main())
