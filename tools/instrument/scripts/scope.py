#!/usr/bin/env python
"""Rigol 示波器抓取/测量工具 + 正点原子 DS100 CSV 解析（/ea scope）。

两条路径：

A. Rigol 实时 SCPI（PyVISA + SCPI over USB/LAN，DS1000Z / DS2000A 系列）：
   - --detect         探测 pyvisa 后端 / VISA 资源 / *IDN? 识别
   - --list           列出所有 VISA 仪器资源
   - --capture        单次触发 → :WAV:DATA? 抓波形 → TMC 解析 → CSV/PNG
   - --measure vpp,freq,...  直接 SCPI 测量（不抓波形）
   - --parse-tmc <file>  解析原始 TMC 块文件（无硬件自测解析链路）

B. 正点原子 DS100 手持示波器（无 SCPI / 无 PC 软件，USB 仅 U 盘导出）：
   - --parse-ds100 <file.csv>  解析用户在示波器上 M→Save→CSV 导出的原始采样
     自动跳过 header / 探测列（time,volt 两列 或 单列 voltage），输出
     频率/幅值/周期/占空比，可 --plot 出图、--save 规范化。

--dry-run        只打印将执行的调用序列，不真正解析
依赖：numpy（必须）；matplotlib（--plot 才需要）；pyvisa（仅 Rigol 路径）。
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import numpy as np

from common import (
    add_common_args,
    emit_json,
    ensure_matplotlib_agg,
    require_module,
)

# 测量项名映射（展示名 → SCPI 短助记符；两族机型同一套助记符名）
MEASURE_ITEMS = {
    "vmax": "VMAX", "vmin": "VMIN", "vpp": "VPP", "vtop": "VTOP",
    "vbase": "VBASe", "vamp": "VAMP", "vrms": "VRMS", "vavg": "VAVG",
    "freq": "FREQ", "period": "PERIod",
    "pwidth": "PWIDth", "nwidth": "NWIDth", "pduty": "PDUTy", "nduty": "NDUTy",
    # rise / fall：DS1074Z fw 00.04.02.SP3 与 DS2302A fw 00.03.06 上 RISE /
    # RISetime / FALL / FALLtime 全都**不被识别**——而两台对不认识的助记符
    # 都既不报错也不回复，主机只能阻塞到超时，于是这一项会静默变成 N/A。
    # 故不放进默认名单，需要时用 --measure rise 由 meas_item 报"该机型不支持"。
    "rise": "RISetime", "fall": "FALLtime",
}
# 实测两族都不支持的项（避免默认名单里放进注定超时的项）：
# rise/fall —— DS1074Z fw 00.04.02.SP3 与 DS2302A fw 00.03.06 都无响应。
UNSUPPORTED_ITEMS: set[str] = {"rise", "fall"}

# 无有效测量时仪器返回 9.9E37 这个哨兵值，必须当成"无数据"而非数字
INVALID_SENTINEL = 1e30

# 横向格数：两族实测都是 **100 点/格**（DS1074Z 1200 点/12 格，DS2302A
# 1400 点/14 格）。scope.py 原先写死 12 格，换到 DS2302A 后每一次抓取都会
# 误报"时间轴不自洽"，所以改成问仪器要屏幕点数再除。
SCREEN_POINTS_PER_DIV = 100

# 测量命令形式探测用的短超时（ms）。猜错形式只白等这一次，不影响结果。
PROBE_TIMEOUT_MS = 600

# 走**直查形式**（:MEAS:<item>? <ch>）的机型前缀。DS2000A 上 :MEAS:ITEM 这个
# 节点根本不存在（:SYST:ERR? 回 -113 Undefined header）。这只是一个猜测，
# 最终由 probe 实测确认——猜错就换另一种形式，不会给出错数字。
_DIRECT_FORM_FAMILIES = ("DS2", "DS4", "DS6", "DS7",
                         "MSO2", "MSO4", "MSO5", "MSO7")

TMC_HEADER = b"#"


def ensure_pyvisa(args, ns=None):
    """拿到 pyvisa 并构造 ResourceManager；后端解析：
    显式 --backend → 用指定后端，失败回退系统 VISA；
    未指定 → 系统 VISA 优先（USB-TMC 需系统后端/NI-VISA），无资源回退 @py（网口场景）。"""
    ns = ns or require_module("import pyvisa", "pyvisa", "pyvisa")
    if ns is None:
        sys.exit(1)
    pyvisa = ns["pyvisa"]
    if args.backend:
        try:
            return pyvisa.ResourceManager(args.backend)
        except Exception as exc:  # noqa: BLE001 - 回退系统 VISA
            print(f"⚠️  后端 {args.backend} 不可用（{exc}），回退系统 VISA")
    try:
        rm = pyvisa.ResourceManager()  # 系统 VISA（NI-VISA / RIGOL IVI 等）
        if rm.list_resources():
            return rm
        print("⚠️  系统 VISA 无资源，回退 @py 后端")
        return pyvisa.ResourceManager("@py")
    except Exception as exc:  # noqa: BLE001 - 未装系统 VISA
        print(f"⚠️  系统 VISA 不可用（{exc}），回退 @py 后端")
        return pyvisa.ResourceManager("@py")


def list_resources(rm) -> list[str]:
    try:
        return list(rm.list_resources())
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  列资源失败：{exc}")
        return []


def open_instrument(rm, resource: str, args) -> object:
    inst = rm.open_resource(resource)
    inst.timeout = args.timeout
    try:
        inst.chunk_size = args.chunk  # DS1000Z USB 稳定性
    except (AttributeError, ValueError):
        pass
    return inst


def identify(inst) -> str:
    resp = inst.query("*IDN?").strip()
    if resp.lower().startswith("unexpected") or "no such" in resp.lower():
        raise ValueError(f"*IDN? 异常响应: {resp!r}")
    return resp


def find_rigol(rm, resource: str | None) -> str:
    """选 resource：显式指定，或遍历资源挑 *IDN? 含 RIGOL 的。"""
    resources = list_resources(rm)
    if resource:
        return resource
    for res in resources:
        try:
            inst = open_instrument(rm, res, args_placeholder())
            idn = identify(inst).upper()
            inst.close()
            if "RIGOL" in idn:
                return res
        except Exception:  # noqa: BLE001 - 跳过不可达资源
            continue
    # 兜底：找不到 RIGOL 就用第一个资源（并打印警告）
    if resources:
        print(f"⚠️  未发现 RIGOL 设备，使用第一个资源 {resources[0]}")
        return resources[0]
    raise RuntimeError("无可用 VISA 资源：示波器未插 / 未装 USB-TMC 驱动 / 后端缺失")


def args_placeholder():
    """open_instrument 的默认 args 值。"""
    class _A:
        timeout = 1500
        chunk = 32
    return _A()


def drain(inst) -> None:
    """丢弃读缓冲里残留的回复。

    DS1000Z 上"查询超时"不等于"不会回"——命令只是晚回。实测 :AUT 期间轮询
    :TIM:SCAL? 会攒下 6 个迟到回复，AUT 结束后一次性涌入，此后每次读取都
    错位一格：:CHAN1:OFFS? 读到了 :CHAN1:COUP? 的 'DC'（float 直接抛
    could not convert string to float），或读到一个早已过期的电压值。
    """
    try:
        from pyvisa import constants

        inst.flush(constants.VI_READ_BUF)
        return
    except Exception:  # noqa: BLE001 - 后端不支持 flush 时退回读空
        pass
    old = inst.timeout
    try:
        inst.timeout = 150
        while True:
            try:
                if not inst.read_raw():
                    break
            except Exception:  # noqa: BLE001 - 没有更多数据可读
                break
    finally:
        inst.timeout = old


def wait_ready(inst, deadline_s: float = 20.0) -> bool:
    """等长命令（:AUT）执行完，用 *OPC? 而不是轮询。

    本机型 *OPC? 实测可用且真的会等 :AUT 结束（实测 6.9s 后才回 '1'），
    之后读缓冲是干净的。轮询方案会把迟到的回复堆在缓冲里，必须避免。
    """
    old = inst.timeout
    ok = False
    try:
        inst.timeout = int(deadline_s * 1000)
        inst.write("*OPC?")
        ok = inst.read().strip().startswith("1")
    except Exception:  # noqa: BLE001 - 部分固件 OPC 时序宽松
        ok = False
    finally:
        inst.timeout = old
        drain(inst)
    return ok


def configure_frontend(inst, args) -> tuple[float, dict]:
    """前端配置：自动设置 / 时基 / 垂直灵敏度 / 偏置 / 耦合。

    返回 (vdiv, applied)，applied 记录**实际生效**的值（SCPI 写入会被仪器
    按档位取整，回读才知道真实值）。--voffs 与 --timebase 以前是死参数：
    前者只进了本地的解码公式、没写进仪器，后者压根没用。
    """
    applied: dict = {}
    if args.autoset:
        inst.write(":AUT")
        wait_ready(inst, 15.0)  # :AUT 期间命令解析器被占，查询会一直超时

    if args.timebase is not None:
        inst.write(f":TIM:SCAL {args.timebase:g}")
    # 时基一律回读（不只是 --timebase 时）：时间轴自洽检查要拿它跟
    # xinc×点数 对照，缺了这项检查就整个失效。
    applied["timebase_s"] = float(inst.query(":TIM:SCAL?"))
    if args.coupling:
        inst.write(f":{args.channel}:COUP {args.coupling}")
        applied["coupling"] = inst.query(f":{args.channel}:COUP?").strip()

    if args.vdiv is None:
        vdiv = float(inst.query(f":{args.channel}:SCAL?"))
    else:
        inst.write(f":{args.channel}:SCAL {args.vdiv:g}")
        vdiv = float(inst.query(f":{args.channel}:SCAL?"))
    if args.voffs is not None:
        inst.write(f":{args.channel}:OFFS {args.voffs:g}")
    elif args.vdiv is not None:
        # 指定了 --vdiv 就把偏置归零：DS1000Z 换档时会**按比例缩放**残留偏置
        # （实测 0.5→1 V/div 把 -2.41V 变成 -4.82V），残留值会让同一命令
        # 每次跑出不同结果，甚至把 3.3V 信号整体推到屏幕外。
        inst.write(f":{args.channel}:OFFS 0")
    applied["vdiv"] = vdiv
    applied["voffs"] = float(inst.query(f":{args.channel}:OFFS?"))
    # 探头比要报出来：它决定"读数到底是不是真实电压"。实测踩过——探头物理
    # 档位在 10×，而仪器 :PROB? 仍是 1，于是一个 3.3V 的逻辑高电平被读成
    # 0.35V，输出里没有任何字段能让人看出问题。
    try:
        applied["probe"] = float(inst.query(f":{args.channel}:PROB?").strip())
    except Exception:  # noqa: BLE001 - 读不到就不报，不打断抓取
        applied["probe"] = None
    return vdiv, applied


def probe_hdiv(inst) -> float | None:
    """横向格数 = 屏幕点数 / 100，取自仪器而不是写死 12。

    DS1074Z 是 12 格（1200 点），DS2302A 是 14 格（1400 点）——两族都是
    100 点/格，所以不需要按型号分支。**必须在 NORM 下读**：RAW 模式下
    :WAV:POIN? 回的是深内存点数（DS2302A 上实测 700000），除出来是 7000 格，
    会让时间轴自洽检查整个失效。读完恢复原来的模式，不干扰后续抓取。
    """
    try:
        old_mode = inst.query(":WAV:MODE?").strip()
    except Exception:  # noqa: BLE001
        old_mode = "NORM"
    try:
        inst.write(":WAV:MODE NORM")
        npts = float(inst.query(":WAV:POIN?").strip().split(",")[0])
    except Exception:  # noqa: BLE001 - 读不到就不做这项检查，别用假值误报
        return None
    finally:
        try:
            inst.write(f":WAV:MODE {old_mode}")
        except Exception:  # noqa: BLE001
            pass
    return npts / SCREEN_POINTS_PER_DIV if npts > 0 else None


def channel_exists(inst, channel: str) -> bool:
    """通道名是否真实存在；不存在时打印可操作的报错并返回 False。

    实测：DS2302A 只有 2 通道，`--channel CHAN3` 在写 `:CHAN3:SCAL` 时**不报
    错**，一路走到后面的查询才超时，报出来只有一句光秃秃的 VI_ERROR_TMO，
    看不出是通道名的问题。CHAN1/CHAN2 是两族共同持有的，直接放行不做多
    余往返；CHAN3/4 才探一次——**探完必须 drain**：DS2302A 实测主机侧超时
    后仪器会把 -410,"Query INTERRUPTED" 排进队列，不排空会污染后续读取。
    """
    n = int(str(channel).upper().replace("CHAN", "") or 0)
    if n <= 2:
        return True
    if _query_short(inst, f":CHAN{n}:SCAL?") is not None:
        return True
    drain(inst)
    print(f"❌ 通道 {channel} 在本机不存在（:CHAN{n}:SCAL? 无响应）——"
          f"DS1000Z 有 4 通道、DS2000A 只有 2 通道，请核对 --channel")
    return False


def setup_acquisition(inst, ch: str, points: int | None, mode: str) -> None:
    """WAVeform 配置：数据源 / 编码 / 模式 / 深度 / 读取区间。"""
    inst.write(":WAV:SOUR " + ch)
    inst.write(":WAV:FORM BYTE")
    inst.write(":WAV:MODE " + mode)  # NORM(屏幕点数：12/14 格×100) / RAW(深内存)
    if mode.upper() == "RAW":
        # RAW 模式必须同时给深度和 STAR/STOP 区间。只给 STAR/STOP 而不写
        # :ACQ:MDEP，深内存不会生效（实测仍回 1200 点）。
        depth = points or 12000
        inst.write(f":ACQ:MDEP {depth}")
        # STOP 不能超过**实际**采集深度：DS2302A 的 :ACQ:MDEP 由时基反推
        # （5e-5 s/div × 14 格 × 1 GSa/s = 700000），写 1200000 进去读回还是
        # 700000，此时 :WAV:STOP 1200000 就越界。所以按回读值夹一下。
        try:
            actual = int(float(inst.query(":ACQ:MDEP?").strip().split(",")[0]))
        except Exception:  # noqa: BLE001 - 回读不到就按请求值走
            actual = depth
        stop = min(depth, actual) if actual > 0 else depth
        inst.write(":WAV:STAR 1")
        inst.write(f":WAV:STOP {stop}")
    elif points:
        # NORM 只回屏幕点数，把 MDEP 设得比屏幕大反而会拿到空块——明确拒绝
        raise ValueError(
            "NORM 模式只能取屏幕点数，--points 请与 --mode RAW 一起用")


def single_shot(inst) -> None:
    """单次触发并等待完成（*OPC? 阻塞直到就绪）。"""
    inst.write(":ACQ:MODE NORM")
    inst.write(":TRIG:MOD SING")
    inst.write(":SINGle")
    try:
        inst.query("*OPC?")
    except Exception:  # noqa: BLE001 - 部分固件 OPC 时序宽松
        pass


def read_waveform(inst) -> bytes:
    """:WAV:DATA? 用 read_raw 取二进制 TMC 块（勿用 query，会 UnicodeDecodeError）。"""
    inst.write(":WAV:DATA?")
    return inst.read_raw()


def tune_deep_read(inst, points: int) -> tuple:
    """深内存读取前临时调大 chunk 和 timeout，返回旧值供还原。

    1. **chunk**：`--chunk` 默认 32 是给 DS1000Z 屏幕读取（1200 点）加的 USB
       稳定性设置，但深内存动辄几十万点——实测 DS2302A 读 140KB：
       chunk=32 要 6.16s，chunk≥1024 只要 0.63s，**差 10 倍**；1.4MB 在 32 下
       直接撞默认 3s 超时读不完。故 RAW 下一次读取临时放大到 65536。
       放大块不影响正确性：实测 1024 / 65536 / 1048576 三种块大小读回的
       sum/min/max 完全相同。
    2. **timeout**：140 万点实测 6.25s，固定 3s 必然失败。按 9us/点（实测
       4.5us/点的两倍余量）+ 2s 底数算，够 1.4MB 用。
    """
    old_chunk = getattr(inst, "chunk_size", None)
    old_timeout = inst.timeout
    if old_chunk is not None and old_chunk < 4096:
        try:
            inst.chunk_size = 65536
        except (AttributeError, ValueError):
            pass
    need = int(2000 + max(1, points) * 0.009)
    if need > old_timeout:
        inst.timeout = need
        print(f"  ⏱️  深内存 {points} 点：读取超时临时放宽到 {need / 1000:.1f}s")
    return old_chunk, old_timeout


def restore_deep_read(inst, saved: tuple) -> None:
    """还原 tune_deep_read 改过的传输参数（不影响后续测量命令）。"""
    old_chunk, old_timeout = saved
    inst.timeout = old_timeout
    if old_chunk is not None:
        try:
            inst.chunk_size = old_chunk
        except (AttributeError, ValueError):
            pass


def parse_tmc_block(raw: bytes) -> bytes:
    """解析 TMC 二进制块 #NXXXXXX<data>：N 位 ASCII 长度 + 精确切片。"""
    if not raw or raw[:1] != TMC_HEADER:
        raise ValueError(f"TMC 块头错误：期望 '#'，实际 {raw[:4]!r}")
    n = int(chr(raw[1]))  # 长度字段位数
    if n == 0:
        raise ValueError(
            "示波器回的是空数据块 #0。常见原因：NORM 模式下 :ACQ:MDEP 设得比"
            "屏幕点数大，或 :SINGle 未触发成功——改用 :RUN 连续采集，"
            "深内存请用 --mode RAW 配合 --points")
    if 2 + n > len(raw):
        raise ValueError(f"TMC 长度头截断：n={n}，raw 长度 {len(raw)}")
    length = int(raw[2:2 + n].decode("ascii"))
    data = raw[2 + n:2 + n + length]
    if len(data) < length:
        raise ValueError(f"TMC 数据截断：声明 {length}，实际 {len(data)}")
    return data


def read_preamble(inst, vdiv: float) -> dict:
    """读 :WAV: 前导，供电压/时间轴还原。

    实测 DS1074Z fw 00.04.02.SP3：YREF=127（**不是 128**）、YINC=vdiv/25、
    YOR=25*OFFS（单位是**码值**，不是伏特）。全部回读，不靠写死常量。
    """
    def q(cmd: str, default: float) -> float:
        try:
            return float(inst.query(cmd).strip().split(",")[0])
        except Exception:  # noqa: BLE001 - 前导读不到就退回缺省，不打断抓取
            return default

    return {
        "yref": q(":WAV:YREF?", 127.0),
        "yinc": q(":WAV:YINC?", vdiv / 25.0),
        "yor": q(":WAV:YOR?", 0.0),
        "xinc": q(":WAV:XINC?", 0.0),
        "xorig": q(":WAV:XOR?", 0.0),
    }


def decode_to_volts(payload: bytes, vdiv: float, voffs: float,
                    pre: dict | None = None) -> np.ndarray:
    """BYTE 格式还原电压：V = (code - YREF - YOR) * YINC。

    实测三种偏置下该式与仪器自报的 VMIN/VPP **完全一致**：
    OFFS=0 时 (135-127-0)*0.04=0.32V，OFFS=+1 时 (160-127-25)*0.04=0.32V，
    OFFS=-1 时 (110-127+25)*0.04=0.32V——同一电压读数不随偏置漂移。
    原先写死的 (code-128)/25*vdiv + voffs 在 OFFS=0 时偏低 1 个码值(0.04V)，
    且 voffs 加在解码公式里而不是写进仪器，偏置越大错得越多。
    pre 为 None 时（无仪器的 --parse-tmc）回退旧行为。
    """
    vals = np.frombuffer(payload, dtype=np.uint8).astype(np.float64)
    if pre is None:
        return (vals - 128.0) / 25.0 * vdiv + voffs
    yinc = pre.get("yinc") or (vdiv / 25.0)
    return (vals - pre.get("yref", 127.0) - pre.get("yor", 0.0)) * yinc


# 满码 0..255；贴到轨道即削顶（信号超出屏幕，解码值不可信）
CLIP_LO_CODE, CLIP_HI_CODE = 0, 255
# 码值跨度低于此值视为平线（通道关闭/无信号）；1~3 个码值是数字化噪声的典型幅度
FLAT_CODE_SPAN = 8
# 上升沿间隔的变异系数（std/mean）超过此值即认为信号不等周期。
# 阈值取 0.15 的依据：真机 41 个相位样本实测 cv 最大 0.0279（P1 0x55，
# 周期只有 34.7 个采样点，量化占比最大），有 5 倍余量不会误报；
# 而合成波形里跨相位切换是 0.82、2 采样点窄毛刺是 0.18，都被挡住。
IRREGULAR_CV = 0.15


def analyze_samples(payload: bytes, volts: np.ndarray, xinc: float,
                    yinc: float) -> dict:
    """采样域波形分析：频率/占空比 + 削顶/无信号判定。

    频率用"跨度法"（首末上升沿之间除以周期数），不用逐周期取中位数：
    后者会被采样网格量化——实测时基 2us/点时逐周期法的 PERI 误差 -3.55%，
    而跨度法只有 -0.12%；示波器自己的 :MEAS:ITEM? FREQ 也是按网格量化的
    （36.00us vs 真值 34.76us），精度比跨度法低约一个数量级。
    占空比同样只数首末上升沿之间的**整数个周期**，见下方注释。
    """
    res: dict = {"edges": 0, "freq_hz": None, "period_s": None, "duty_pct": None,
                 "duty_method": None, "clipped": False, "clip_low": False,
                 "clip_high": False, "flat": False, "noisy": False,
                 "irregular": False, "period_cv": None,
                 "code_span": None, "periods": 0}
    codes = np.frombuffer(payload, dtype=np.uint8)
    if len(codes):
        res["clip_low"] = bool(codes.min() <= CLIP_LO_CODE)
        res["clip_high"] = bool(codes.max() >= CLIP_HI_CODE)
        res["clipped"] = res["clip_low"] or res["clip_high"]
        span_codes = float(codes.max() - codes.min())
    else:
        span_codes = None

    vpp = float(volts.max() - volts.min())
    if span_codes is None:
        span_codes = vpp / (yinc or 1e-12)
    res["code_span"] = round(span_codes, 3)
    # 平线判据用**码值跨度**而不是伏特：通道关掉时屏幕上只剩 1~3 个码值的
    # 数字化噪声，按伏特判会被 --vdiv 缩放带偏——实测 CHAN2 悬空时 0.6mV 的
    # 噪声被算成 3125Hz "信号"。真信号在屏幕上至少占十几个码值。
    if span_codes <= FLAT_CODE_SPAN:
        res["flat"] = True  # 平线：通道关闭 / 无信号 / 探头没夹上
        res["edges"] = 0
        return res

    mid = float(volts.min() + vpp / 2.0)
    above = volts >= mid
    rise = np.where(np.diff(above.astype(np.int8)) > 0)[0]
    fall = np.where(np.diff(above.astype(np.int8)) < 0)[0]
    res["edges"] = int(len(rise) + len(fall))
    if xinc <= 0:
        return res

    # 频率和占空比都只在**整数个周期**内统计：rise[0]..rise[-1] 之间恰好包含
    # len(rise)-1 个完整周期，首尾同相位，完全不受窗口截断影响。
    #
    # 占空比既不能用"窗口内高电平占比"，也不能用逐段边沿间隔求和：600us 窗口
    # （时基 5e-5）里只有 3.46 个 173.6us 周期，多算/少算**一个**完整的低电平
    # 脉冲就是 17.36/600 = ±2.9% 的占空比误差——比任何阈值算法的差别都大。
    # 实测 0xFF（真值 90.0%，示波器自报 PDUT 89.94%）：段求和法给 94.85%，
    # 窗口均值法给 91.33%，只数整数周期才给出 90.00%。
    if len(rise) >= 2:
        n = int(rise[-1] - rise[0])
        span = float(n * xinc)
        res["periods"] = int(len(rise) - 1)
        res["period_s"] = round(span / (len(rise) - 1), 12)
        res["freq_hz"] = round((len(rise) - 1) / span, 3)
        if n > 0:
            res["duty_pct"] = round(
                float(np.count_nonzero(above[rise[0]:rise[-1]]) / n * 100.0), 2)
            res["duty_method"] = "整数周期内高电平占比"
        # 周期逼近采样间隔说明"边沿"其实是数字化噪声，不是真信号。
        # 实测 CHAN2 悬空、5mV/div 时 8mV 噪声跨度 40 个码值（过得了平线判据），
        # 被算成 "62605Hz 方波"——加这条按采样极限挡住。
        if res["period_s"] < 10.0 * xinc:
            res["noisy"] = True
        # 边沿间隔是否整齐。跨度法隐含"信号等周期"这个前提，而它在跨相位切换、
        # 突发、含毛刺的窗口上会失效——却照样给出一个**看似自信的假频率**。
        # 实测：DS2302A 深内存 700us 窗口正好跨过信号发生器的相位切换点，
        # 上升沿间隔从 2 到 288 个采样点不等，仍然算出 "7674Hz" 这种没有意义
        # 的值，而工具当时只当成正常结果输出。
        if len(rise) >= 3:
            d = np.diff(rise).astype(np.float64)
            if d.mean() > 0:
                res["period_cv"] = round(float(d.std() / d.mean()), 4)
                res["irregular"] = bool(res["period_cv"] > IRREGULAR_CV)
    elif len(above):
        # 一个上升沿都没有：只能退回窗口均值，并说明它相位有偏。
        res["duty_pct"] = round(float(np.mean(above)) * 100.0, 2)
        res["duty_method"] = "window-mean(边沿不足,窗口相位有偏)"
    return res


def build_time_axis(n: int, pre: dict) -> np.ndarray:
    """时间轴：XOR + i*XINC。"""
    xinc = pre.get("xinc") or 1e-6
    return pre.get("xorig", 0.0) + np.arange(n) * xinc


def save_csv(times: np.ndarray, volts: np.ndarray, path: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(out, np.column_stack((times, volts)), delimiter=",",
               header="time_s,voltage_v", comments="")
    print(f"  ✅ 波形 CSV → {out}")


def plot_waveform(times: np.ndarray, volts: np.ndarray, png: str, ch: str) -> None:
    import matplotlib.pyplot as plt

    ensure_matplotlib_agg()
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(times * 1e3, volts, lw=0.8)
    ax.set_xlabel("time (ms)")
    ax.set_ylabel("voltage (V)")
    ax.set_title(f"{ch} waveform")
    ax.grid(True, alpha=0.3)
    fig.savefig(png, dpi=120)
    plt.close(fig)
    print(f"  ✅ 波形图 → {png}")


def parse_items(args) -> list[str] | None:
    """解析 --measure 列表；出现未知项时打印支持列表并返回 None。"""
    items = [i.strip().lower() for i in args.measure.split(",") if i.strip()]
    unknown = [i for i in items if i not in MEASURE_ITEMS]
    if unknown:
        print(f"❌ 未知测量项 {unknown}（支持：{', '.join(sorted(MEASURE_ITEMS))}）")
        return None
    return items


# 探测结果缓存在进程内：每次运行只问一次仪器，不重复付超时代价。
_MEAS_STATE: dict = {"form": None, "idn": None}


def _query_short(inst, cmd: str) -> str | None:
    """短超时查询；超时返回 None（探测阶段超时是**预期结果**，不是错误）。"""
    old = inst.timeout
    inst.timeout = PROBE_TIMEOUT_MS
    try:
        return inst.query(cmd).strip()
    except Exception:  # noqa: BLE001 - 探测：不回就是"不支持"
        return None
    finally:
        try:
            inst.timeout = old
        except Exception:  # noqa: BLE001
            pass


def _as_float(resp: str | None) -> float | None:
    """能把回复解析成数字就算这个形式可用（哨兵 9.9E37 也算"回了"）。"""
    if resp is None:
        return None
    try:
        return float(resp.split(",")[0])
    except ValueError:
        return None


def _meas_cmds(form: str, scpi: str, channel: str) -> tuple[str | None, str]:
    """按形式给出 (预备写命令 | None, 查询命令)。

    item   形式（DS1000Z）：先 :MEAS:ITEM <item>,<ch> 选中，再查同一对参数；
    direct 形式（DS2000A）：:MEAS:ITEM 节点不存在，只有 :MEAS:<item>? <ch>，
           通道号直接做后缀，不必改 :MEAS:SOUR 状态。
    """
    if form == "direct":
        return None, f":MEAS:{scpi}? {channel}"
    return f":MEAS:ITEM {scpi},{channel}", f":MEAS:ITEM? {scpi},{channel}"


def probe_meas_form(inst, channel: str) -> str:
    """确定本机的测量命令形式并缓存，返回 "item" 或 "direct"。

    两族形式互斥，且都不报错——DS1000Z 对不认识的助记符**既不回复也不进错误
    队列**，DS2000A 则是在错误队列里留一条 -113 后同样不回复。所以只能实测：
    按 *IDN? 给个初步猜测，再用 600ms 短超时各发一次 VPP 查询，谁能解析成
    数字就用谁。猜错只多花一次 0.6s 超时，不会产出错数字。
    """
    if _MEAS_STATE["form"]:
        return _MEAS_STATE["form"]
    idn = _MEAS_STATE.get("idn") or ""
    if not idn:
        try:
            idn = inst.query("*IDN?").strip()
        except Exception:  # noqa: BLE001 - 探测失败不影响后面报错
            idn = ""
        _MEAS_STATE["idn"] = idn
    model = idn.split(",")[1].strip().upper() if idn.count(",") >= 2 else ""
    order = ["item", "direct"]
    if model.startswith(_DIRECT_FORM_FAMILIES):
        order.reverse()
    # :STOP 状态下部分项只会回哨兵，先确保在连续采集
    try:
        inst.write(":RUN")
    except Exception:  # noqa: BLE001
        pass
    for form in order:
        write_cmd, query_cmd = _meas_cmds(form, "VPP", channel)
        if write_cmd:
            try:
                inst.write(write_cmd)
            except Exception:  # noqa: BLE001
                continue
        if _as_float(_query_short(inst, query_cmd)) is not None:
            _MEAS_STATE["form"] = form
            return form
    # 两种形式都不回：保留首选，让后续的超时诊断（读错误队列）去说明原因
    _MEAS_STATE["form"] = order[0]
    return order[0]


def read_error(inst) -> str | None:
    """读 :SYST:ERR? 队列首条；读不到就返回 None。

    DS2302A 实测可用（链路干净时回 `0,"No error"`），能把"查询超时"从模糊
    的"可能不支持"变成确定诊断：读到 -113,"Undefined header; keyword cannot
    be found"，直接证明命令节点不存在。DS1000Z 上该命令不回。

    **不缓存"不支持"的结论**：DS2302A 实测，主机侧查询超时后仪器会把
    `-410,"Query INTERRUPTED"` 排进队列，紧接着的一次 :SYST:ERR? 会先**超时
    一次**、再回真实条目（已复现）。也就是说一次链路抖动就能把"机型不支持"
    的错误结论缓存下来，让本来支持的机型在整个会话里丢掉这条诊断。本函数
    只在失败路径调用（每个失败项一次），每次多花一个 600ms 短超时换取诊断
    可靠，是划算的。
    """
    return _query_short(inst, ":SYST:ERR?")


def meas_item(inst, scpi: str, channel: str, tries: int = 3) -> tuple[float | None, str | None]:
    """读一项内置测量，返回 (值 | None, 失败原因 | None)。

    三个必须区分的坑：
    1. 测量无效时仪器回 **9.9E37** 哨兵，直接 float() 会得到一个荒谬但
       "看起来正常"的大数（老版本原样输出 vpp=9.9e+37）；
    2. 助记符不被该机型识别时，仪器**既不报错也不回复**，主机阻塞到超时——
       所以必须重试，并把"超时"与"无效"分开报；
    3. 命令形式的机型差异（见 probe_meas_form）——原先写死 DS1000Z 的
       :MEAS:ITEM? 形式，在 DS2302A 上**每一项都超时**，--measure 完全不可用。
    """
    form = probe_meas_form(inst, channel)
    write_cmd, query_cmd = _meas_cmds(form, scpi, channel)
    last: Exception | None = None
    for k in range(tries):
        try:
            if write_cmd:
                inst.write(write_cmd)
            resp = inst.query(query_cmd).strip()
        except Exception as exc:  # noqa: BLE001 - 超时/链路抖动都重试
            last = exc
            # 超时可能只是"晚回"：先排空缓冲，否则重试读到的会是上一条的回复
            drain(inst)
            time.sleep(0.25 * (k + 1))
            continue
        try:
            val = float(resp)
        except ValueError:
            return None, f"响应无法解析：{resp!r}"
        if abs(val) >= INVALID_SENTINEL:
            return None, (
                "测量无效（仪器回 9.9E37 哨兵）。成因有三类，别只当作"
                "\"超量程\"：①信号被 Y 方向削顶/推到屏幕外；②**幅度相对 V/div "
                "太小**——实测 DS2302A 上 0.35Vpp 打在 1V/div（纵向仅 0.35 格）"
                "时 VPP 正常回数，而 FREQ/PDUTy/PWIDth **全部回哨兵**，降到 "
                "0.5V/div 就都正常了；③确实无有效边沿/周期超出测量范围。"
                "先看 vpp 是否正常，正常就调小 --vdiv 再测")
        return val, None
    err = read_error(inst)
    hint = f"；仪器错误队列：{err}" if err else ""
    return None, (f"查询超时 {tries} 次（{form} 形式 {query_cmd}）"
                  f"——该助记符/命令形式可能不被本机型支持{hint}，"
                  f"底层错误 {last}")


def collect_measurements(inst, args, items: list[str]) -> dict:
    """按 --measure 列表逐项取值，失败原因单独收集。"""
    inst.write(":RUN")
    time.sleep(0.5)  # 等测量引擎刷新一帧
    values: dict = {}
    for item in items:
        # 已知该机型不支持的项只试一次：不认识的助记符只能靠超时发现，
        # 重试 3 次要白等 10 秒以上
        tries = 1 if item in UNSUPPORTED_ITEMS else 3
        val, why = meas_item(inst, MEASURE_ITEMS[item], args.channel, tries=tries)
        if item in UNSUPPORTED_ITEMS:
            why = (f"实测两族机型都不支持 {MEASURE_ITEMS[item]}"
                   f"（DS1074Z fw 00.04.02.SP3 与 DS2302A fw 00.03.06 上 "
                   f"RISE/RISetime/FALL/FALLtime 均无响应）——改用采样域测量："
                   f"--capture 后用 sample_domain，或 --measure pwidth/pduty 推算")
        values[item] = val
        if why:
            print(f"  ⚠️  {item}: {why}")
    return values


def _waveform_warnings(pre: dict, smp: dict, applied: dict, args,
                       npts: int) -> list[str]:
    """把"数据不可信"的情况明说，而不是给一个看起来很确定的数字。"""
    warn: list[str] = []
    if smp["clipped"]:
        rail = "上" if smp["clip_high"] else "下"
        warn.append(
            f"波形贴到屏幕{rail}轨（码值触到 0/255），Y 方向已削顶，"
            f"Vpp/Vmin/Vmax 全部偏小——加大 --vdiv（当前 "
            f"{applied.get('vdiv')} V/div）或用 --voffs 把波形拉回屏幕内")
        if smp["freq_hz"] is not None:
            warn.append("削顶会产生虚假边沿，频率/占空比同样不可信")
    if smp["flat"]:
        warn.append(
            f"波形是平线（码值跨度仅 {smp.get('code_span')}）：通道可能没开 / "
            f"探头没夹上 / 信号源没输出——不要当成'测到了 0V'")
    if smp.get("noisy"):
        warn.append(
            f"测出的周期（{smp['period_s'] * 1e6:.2f}us）不足采样间隔的 10 倍，"
            f"这些'边沿'多半是数字化噪声而不是真信号（悬空通道典型表现）"
            f"——请确认探头接好，或把 --vdiv 调大降低噪声占比")
    if smp.get("irregular"):
        warn.append(
            f"上升沿间隔不均匀（变异系数 {smp['period_cv']}）：信号不等周期——"
            f"窗口跨了相位切换/突发/含毛刺。此时 freq_hz 和 duty_pct 是整段"
            f"窗口的平均值，**不要当成信号的周期**；缩窄窗口或改用 --measure "
            f"看仪器单周期结果")
    duty = smp.get("duty_method") or ""
    if duty.startswith("window-mean"):
        warn.append("窗口内边沿不足，占空比按窗口均值估算，受窗口起点相位影响")
    # 时间轴自洽检查。两种模式的判据**不一样**，不能用同一条：
    #   NORM：xinc×点数 就是整屏窗口，必须**等于** 时基×横向格数；
    #   RAW ：xinc 变成深内存的采样间隔（DS2302A 上 5e-10 vs 屏幕 1e-6），
    #         点数只是用户要的**子窗口**，xinc×点数 天然远小于整屏——
    #         这里只能检查它有没有**超出**采集窗口，不能要求相等。
    # 原先不分模式一律按等于判，RAW 下每次抓取都误报"时间轴不自洽"。
    # 格数取自仪器（DS1074Z 12 格 / DS2302A 14 格），不再写死 12。
    tb = applied.get("timebase_s")
    hdiv = applied.get("hdiv")
    raw_mode = args.mode.upper() == "RAW"
    if tb and hdiv and npts and pre["xinc"] > 0:
        expect = tb * hdiv
        actual = pre["xinc"] * npts
        if raw_mode:
            if actual > expect * 1.05:
                warn.append(
                    f"深内存区间超出采集窗口：xinc×点数={actual * 1e6:.1f}us > "
                    f"时基×{hdiv:g}格={expect * 1e6:.1f}us——XINC 与点数不是"
                    f"同一次采集的，频率/周期按当前 XINC 换算会偏")
        elif abs(actual - expect) / expect > 0.05:
            warn.append(
                f"时间轴不自洽：xinc×点数={actual * 1e6:.1f}us，"
                f"时基×{hdiv:g}格={expect * 1e6:.1f}us——抓到的点数与当前 XINC "
                f"不是同一次采集（深内存没生效时就是这样），频率/周期按当前 "
                f"XINC 换算会偏")
    # RAW 没拿到深内存：回读点数明显少于请求，说明 :WAV:MODE RAW 没生效。
    # 已知成因：仪器不在 STOP 状态（RUN 期间只吐屏幕记录，两族实测都是），
    # 或该机型深内存档位不可写。只在**确实短了**的时候说，否则会误报——
    # DS2302A 上 RAW 是能用的，STOP 后同一命令如实回 140000 点。
    if raw_mode and args.points and npts < args.points * 0.9:
        warn.append(
            f"RAW 请求 {args.points} 点却只回 {npts} 点（≈屏幕点数），"
            f"深内存没生效：RAW 必须在 **STOP** 状态下读（RUN 期间仪器只吐"
            f"屏幕记录），且部分机型的 :ACQ:MDEP 不可写")
    # 深内存窗口可能比信号周期还短：DS2302A 上 140000 点 @2GSa/s 只有 70us，
    # 不足一个 173.6us 的 UART 帧，于是"平线"是窗口太短，不是没有信号。
    if raw_mode and smp["flat"]:
        warn.append(
            "RAW 深内存窗口（xinc×点数="
            f"{pre['xinc'] * npts * 1e6:.1f}us）可能短于信号周期，"
            "窗口内没有边沿是正常的——要看到周期请加大 --points")
    return warn


def _measurement_warnings(measured: dict, applied: dict, volts) -> list[str]:
    """把"内置测量没测出来"做成显式警告，并给出可操作的方向。

    实测 DS2302A：0.35Vpp 的信号打在 1V/div 上（纵向仅 0.35 格）时，
    :MEAS:VPP? 正常回数而 FREQ/PDUTy/PWIDth **全部回 9.9E37 哨兵**；
    降到 0.5V/div 就都正常。也就是说时间类测量失败最常见的原因是**幅度
    相对量程太小**，跟"超量程"正好相反——这条不能只靠通用提示。
    """
    warn: list[str] = []
    na = [k for k, v in measured.items() if v is None]
    if not na:
        return warn
    warn.append(f"内置测量未取到值：{', '.join(na)}"
                f"（已置 null，不是 0——不要当成测到了零）")
    vdiv = applied.get("vdiv")
    vpp = float(volts.max() - volts.min())
    time_items = {"freq", "period", "pwidth", "nwidth", "pduty", "nduty"}
    if vdiv and vpp > 0 and (set(na) & time_items) and measured.get("vpp") is not None:
        div_used = vpp / vdiv
        if div_used < 1.0:
            warn.append(
                f"时间类测量失败而 VPP 正常，多半是**纵向占比不足**："
                f"Vpp={vpp:.3f}V 打在 {vdiv:g} V/div 上只占 {div_used:.2f} 格，"
                f"仪器的时间测量引擎出不了结果——把 --vdiv 调到 "
                f"{max(vpp / 3.0, vdiv / 10):g} 附近（让波形占 3 格左右）再测")
    probe = applied.get("probe")
    # 只在该波形**本来够高**时才怀疑探头衰减：一个被 10× 压过的 3.3V 逻辑
    # 信号在 1V/div 上仍占约 8 个码值（3.3/10/(1/25) ≈ 8），而真正的微小纹波
    # 只占几个码值——后者是"信号本来就小"，平线警告已经给了正确诊断，再叠
    # 一条"探头可能在 10×"就是误导（实测还原后的固件 PC10 只有 0.16V 纹波、
    # 码值跨度 4，两条警告同时出现且探头警告是错的）。
    span_codes = 25.0 * vpp / vdiv if vdiv else 0.0
    if vpp < 0.6 and span_codes >= FLAT_CODE_SPAN:
        warn.append(
            f"幅度只有 {vpp:.3f}V 而 :PROB? 读到 {probe:g}"
            + ("——**探头物理档位可能在 10×，而仪器不知道**，读数会小 10 倍；"
               "请核对探头上的 1×/10× 开关" if probe == 1
               else "——请核对探头衰减档位与接线"))
    return warn


def do_capture(args) -> int:
    if args.dry_run:
        _print_scpi_plan(args)
        return 0

    ns = require_module("import pyvisa", "pyvisa", "pyvisa")
    if ns is None:
        return 1
    items = parse_items(args) if args.measure else None
    if args.measure and items is None:
        return 1
    rm = ensure_pyvisa(args, ns)
    try:
        resource = find_rigol(rm, args.resource)
        inst = open_instrument(rm, resource, args)
        with inst:
            idn = identify(inst)
            _MEAS_STATE["idn"] = idn  # 供测量形式探测用，省一次 *IDN?
            print(f"  ▶ {idn}")
            if not channel_exists(inst, args.channel):
                return 1
            vdiv, applied = configure_frontend(inst, args)
            applied["hdiv"] = probe_hdiv(inst)
            raw_mode = args.mode.upper() == "RAW"
            if not raw_mode:
                setup_acquisition(inst, args.channel, args.points, args.mode)
            # 用 :RUN 连续采集（数据总就绪）。勿用 single_shot：:SINGle 未触发
            # 成功时 :WAV:DATA? 回空块 #0，实测复现。
            inst.write(":RUN")
            time.sleep(args.settle)
            if raw_mode:
                # 深内存只能在 STOP 状态下读：RUN 期间即使设了 :WAV:MODE RAW
                # 也照样只回屏幕点数（DS1074Z 实测 --points 12000 仍回 1200 点；
                # DS2302A 同样——STOP 后同一命令如实回 1400/14000/700000 点）
                inst.write(":STOP")
                setup_acquisition(inst, args.channel, args.points, args.mode)
            pre = read_preamble(inst, vdiv)
            saved = (tune_deep_read(inst, args.points or 12000)
                     if raw_mode else None)
            try:
                payload = parse_tmc_block(read_waveform(inst))
            finally:
                if saved:
                    restore_deep_read(inst, saved)
            if raw_mode:
                inst.write(":RUN")  # 恢复屏幕刷新
            volts = decode_to_volts(payload, vdiv, applied["voffs"], pre)
            times = build_time_axis(len(volts), pre)
            smp = analyze_samples(payload, volts, pre["xinc"], pre["yinc"])

            if args.save:
                save_csv(times, volts, args.save)
            if args.plot:
                plot_waveform(times, volts, args.plot, args.channel)
            warnings = _waveform_warnings(pre, smp, applied, args, len(volts))

            measured = collect_measurements(inst, args, items) if items else None
            if measured:
                warnings.extend(_measurement_warnings(measured, applied, volts))
            result = {
                "idn": idn, "channel": args.channel, "mode": args.mode,
                "points": len(volts), "vdiv": applied["vdiv"],
                "voffs": applied["voffs"], "coupling": applied.get("coupling"),
                "probe": applied.get("probe"),
                "timebase_s": applied.get("timebase_s"),
                "xinc_s": pre["xinc"], "yref": pre["yref"], "yinc": pre["yinc"],
                "yorig": pre["yor"],
                "duration_s": round(float(times[-1] - times[0]), 12),
                "min_v": round(float(volts.min()), 6),
                "max_v": round(float(volts.max()), 6),
                "vpp_est": round(float(volts.max() - volts.min()), 6),
                "vavg_est": round(float(volts.mean()), 6),
                "sample_domain": smp,
                "warnings": warnings,
            }
            if measured is not None:
                result["measurements"] = measured
            if args.json:
                emit_json(result)
            else:
                _print_capture(result)

            # 数据不可信时给出非 0 返回码，调用方（含 AI）才不会把警告当噪音。
            # 请求的测量项没测出来同样是"不可信"——否则 --measure 全回 N/A
            # 也返回 0，调用方会把 null 当成正常结果。
            bad = warnings or (measured is not None
                               and any(v is None for v in measured.values()))
            return 2 if bad else 0
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 抓取失败：{exc}")
        return 1
    return 0


def _fmt(v, unit: str, nd: int = 3) -> str:
    return "N/A" if v is None else f"{v:.{nd}f}{unit}"


def _print_capture(r: dict) -> None:
    """人类可读摘要。老版本不带 --json/--save/--plot 时什么都不打印，
    抓取成功与否只能靠退出码猜。"""
    s = r["sample_domain"]
    print(f"  波形  {r['points']} 点 | {r['duration_s'] * 1e3:.3f} ms | "
          f"xinc {r['xinc_s']:g} s | {r['mode']} | "
          f"{r['vdiv']:g} V/div | offs {r['voffs']:g} V"
          + (f" | {r['coupling']}" if r.get("coupling") else "")
          + (f" | PROB×{r['probe']:g}" if r.get("probe") else ""))
    print(f"  电压  Vmin={r['min_v']:.3f} V  Vmax={r['max_v']:.3f} V  "
          f"Vpp={r['vpp_est']:.3f} V  Vavg={r['vavg_est']:.3f} V")
    if s["flat"]:
        print("  时序  平线，无周期信息")
    else:
        per = f" (T={s['period_s'] * 1e6:.3f} us)" if s["period_s"] else ""
        print(f"  时序  频率 {_fmt(s['freq_hz'], ' Hz')}{per}  "
              f"占空比 {_fmt(s['duty_pct'], '%', 2)}  "
              f"边沿 {s['edges']}  [{s['duty_method'] or '-'}]")
    for w in r["warnings"]:
        print(f"  ⚠️  {w}")


def do_measure(args) -> int:
    if args.dry_run:
        _print_measure_plan(args)
        return 0

    ns = require_module("import pyvisa", "pyvisa", "pyvisa")
    if ns is None:
        return 1
    items = parse_items(args)
    if items is None:
        return 1
    rm = ensure_pyvisa(args, ns)
    try:
        resource = find_rigol(rm, args.resource)
        inst = open_instrument(rm, resource, args)
        with inst:
            idn = identify(inst)
            _MEAS_STATE["idn"] = idn  # 供测量形式探测用，省一次 *IDN?
            values = collect_measurements(inst, args, items)
            result = {"idn": idn, "channel": args.channel, "measurements": values}
            if args.json:
                emit_json(result)
            else:
                print(f"  ▶ {idn} | {args.channel}")
                for k, v in values.items():
                    print(f"  {k:8s} = {'N/A（无有效测量）' if v is None else v}")
            # 有项目没测出来 → 非 0，避免 AI 把 N/A 当成正常的 0
            return 2 if any(v is None for v in values.values()) else 0
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 测量失败：{exc}")
        return 1
    return 0


def do_parse_tmc(args) -> int:
    """无硬件自测：解析合成/保存的原始 TMC 块文件。"""
    if args.dry_run:
        print("🔍 dry-run：TMC 块解析流程（不读文件）\n")
        print(f"  读 {args.parse_tmc} → parse_tmc_block（校验 #N 头与长度）")
        print(f"  decode_to_volts（vdiv={args.vdiv or 1.0} voffs={args.voffs or 0.0}）"
              f" → 时间轴 xinc={args.xinc or 1e-6}")
        print("  analyze_samples：频率/占空比 + 削顶/平线判定")
        if args.save:
            print(f"  save_csv → {args.save}")
        if args.plot:
            print(f"  plot_waveform → {args.plot}")
        return 0

    path = Path(args.parse_tmc)
    if not path.is_file():
        print(f"❌ 文件不存在：{path}")
        return 1
    try:
        raw = path.read_bytes()
    except OSError as exc:
        print(f"❌ 读取失败：{exc}")
        return 1
    try:
        payload = parse_tmc_block(raw)
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1
    vdiv = args.vdiv or 1.0
    voffs = args.voffs or 0.0
    volts = decode_to_volts(payload, vdiv, voffs)
    xinc = args.xinc or 1e-6
    times = np.arange(len(volts)) * xinc
    smp = analyze_samples(payload, volts, xinc, vdiv / 25.0)

    print(f"  ✅ TMC 解析成功: 头 {len(raw)}B → 数据 {len(payload)} 点")
    print(f"     电压  Vmin={volts.min():.3f} V  Vmax={volts.max():.3f} V  "
          f"Vpp={volts.max() - volts.min():.3f} V  Vavg={volts.mean():.3f} V")
    if not smp["flat"]:
        print(f"     时序  频率 {_fmt(smp['freq_hz'], ' Hz')}  "
              f"占空比 {_fmt(smp['duty_pct'], '%', 2)}  边沿 {smp['edges']}")
    if smp["clipped"]:
        print(f"     ⚠️  点位触到 0/255 轨道，Y 方向可能削顶")
    if args.save:
        save_csv(times, volts, args.save)
    if args.plot:
        plot_waveform(times, volts, args.plot, "CHAN1")
    if args.json:
        emit_json({"file": str(path), "bytes": len(raw), "points": len(payload),
                   "vdiv": vdiv, "voffs": voffs, "xinc": xinc,
                   "vpp_est": round(float(volts.max() - volts.min()), 6),
                   "sample_domain": smp})
    return 0


# ---------------------------------------------------------------------------
# 正点原子 DS100：手动导出 CSV 波形解析（无 SCPI，仅 U 盘文件）
# ---------------------------------------------------------------------------


# DS100 header 特征：`CHA(V)  probe:X5,sampling rate : 10000`（逗号分隔，值域参考）
_HEADER_SR_RE = re.compile(r"sampling\s*rate\s*[:=]?\s*([\d.]+)\s*([KkMm]?[Hh][Zz])?")
_HEADER_PROBE_RE = re.compile(r"probe\s*[:=]\s*X?([\d.]+)", re.IGNORECASE)


def _parse_header(header_lines: list[list[str]]) -> dict:
    """从 header 文本提取采样率/探头倍率（DS100 真机格式实测：单行逗号分隔）。"""
    meta: dict = {"sampling_rate_hz": None, "probe": None}
    hdr = " ".join(",".join(line) for line in header_lines)
    m = _HEADER_SR_RE.search(hdr)
    if m:
        val = float(m.group(1))
        mult = {"": 1.0, "hz": 1.0, "khz": 1e3, "mhz": 1e6}.get((m.group(2) or "").lower(), 1.0)
        meta["sampling_rate_hz"] = val * mult
    m = _HEADER_PROBE_RE.search(hdr)
    if m:
        meta["probe"] = float(m.group(1))
    return meta


def load_ds100_csv(path: str, time_col: int | None = None,
                   volt_col: int | None = None,
                   xinc: float | None = None) -> tuple[np.ndarray, np.ndarray, str, dict]:
    """解析 DS100 导出的 CSV 波形。

    DS100 无官方格式文档，CSV 为原始采样值。做**弹性解析**：
    逐行跳过非数值行（header），自动探测列结构：
      - 两列且第一列单调递增 → (time, voltage)
      - 两列但第一列不单调 → (序号, voltage)，时间轴用 xinc 推断
      - 单列 → voltage，时间轴由 header 采样率 / xinc 推断
    优先级：显式 --xinc/--sample-rate > header `sampling rate` > 默认 1us。
    --time-col/--volt-col 可显式覆盖自动探测（0 基）。
    返回 (times, volts, mode, meta)，mode ∈ {"2col", "1col", "explicit"}；
    meta 含 {"sampling_rate_hz", "probe"}（来自 header，可能为 None）。
    """
    rows: list[list[float]] = []
    header_lines: list[list[str]] = []
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fp:
        for raw_line in csv.reader(fp):
            if not raw_line:
                continue
            vals: list[float] = []
            ok = True
            for tok in raw_line:
                t = tok.strip().strip('"').strip()
                if not t:
                    ok = False
                    break
                try:
                    vals.append(float(t))
                except ValueError:
                    ok = False
                    break
            if ok:
                rows.append(vals)
            else:
                header_lines.append(raw_line)
    if not rows:
        raise ValueError(f"CSV 无数值行：{path}（不是 DS100 导出？）")

    meta = _parse_header(header_lines)
    ncols = max(len(r) for r in rows)
    explicit = time_col is not None or volt_col is not None
    if time_col is None and volt_col is None:
        if ncols >= 2:
            time_col, volt_col = 0, 1
        elif ncols == 1:
            volt_col = 0
        else:
            raise ValueError(f"CSV 列数异常 {ncols}（支持 1~2 列，或 --time-col/--volt-col 指定）")
    if volt_col is None:
        volt_col = 1

    volts = np.array([r[volt_col] for r in rows if volt_col < len(r)], dtype=np.float64)
    if not len(volts):
        raise ValueError(f"volt 列 {volt_col} 无数据（CSV 共 {ncols} 列）")

    if time_col is not None and ncols > time_col:
        times = np.array([r[time_col] for r in rows if time_col < len(r)], dtype=np.float64)
        if len(times) == len(volts) and len(times) > 1 and np.all(np.diff(times) > 0):
            return times, volts, "explicit" if explicit else "2col", meta

    if xinc is None and meta["sampling_rate_hz"]:
        xinc = 1.0 / meta["sampling_rate_hz"]
    step = xinc if xinc is not None else 1e-6
    return np.arange(len(volts)) * step, volts, "explicit" if explicit else "1col", meta


def analyze_volts(times: np.ndarray, volts: np.ndarray) -> dict[str, float | int | None]:
    """纯 numpy 波形测量：幅值 / 直流 / 有效值 / 频率 / 周期 / 占空比。"""
    res: dict[str, float | int | None] = {
        "points": int(len(volts)),
        "duration_s": round(float(times[-1] - times[0]), 9),
        "vmin": round(float(volts.min()), 6),
        "vmax": round(float(volts.max()), 6),
        "vpp": round(float(volts.max() - volts.min()), 6),
        "vmean": round(float(volts.mean()), 6),
        "vrms": round(float(np.sqrt(np.mean(volts**2))), 6),
        "period_s": None,
        "freq_hz": None,
        "duty_pct": None,
    }
    vpp = res["vpp"]
    if vpp > 1e-9 and len(volts) > 4:
        # 与实时路径共用同一套算法：整数周期内的频率 + 占空比。
        # 逐周期取中位数会被采样网格量化（实测 2us/点时误差 -3.55%，跨度法 -0.12%）。
        step = float(times[1] - times[0]) if len(times) > 1 else 0.0
        smp = analyze_samples(np.zeros(0, dtype=np.uint8), volts, step, vpp / 250.0)
        res["period_s"] = smp["period_s"]
        res["freq_hz"] = smp["freq_hz"]
        res["duty_pct"] = smp["duty_pct"]
        res["duty_method"] = smp["duty_method"]
        res["edges"] = smp["edges"]
    return res


def do_parse_ds100(args) -> int:
    """DS100 CSV 解析：弹性读列 → 测量 → 可选出图/存规范化 CSV/JSON。"""
    if args.dry_run:
        _print_ds100_plan(args)
        return 0
    try:
        times, volts, mode, meta = load_ds100_csv(
            args.parse_ds100, args.time_col, args.volt_col, args.xinc)
        res = analyze_volts(times, volts)
        res["source"] = args.parse_ds100
        col_desc = {"2col": "time,voltage(2col)", "1col": "voltage(1col,xinc推断)",
                    "explicit": f"col{args.time_col}/col{args.volt_col}(显式)"}[mode]
        res["columns"] = col_desc
        res["xinc_s"] = round(float(times[1] - times[0]), 9) if len(times) > 1 else None
        res["header_sampling_rate_hz"] = meta["sampling_rate_hz"]
        res["probe"] = meta["probe"]
        res["file"] = args.parse_ds100

        if args.save:
            save_csv(times, volts, args.save)
        if args.plot:
            plot_waveform(times, volts, args.plot, args.channel or "DS100 CH1")

        if args.json:
            emit_json(res)
            return 0

        print(f"  📄 {args.parse_ds100}  →  {res['points']} 点，{res['columns']}")
        if res["header_sampling_rate_hz"]:
            print(f"     采样率 {res['header_sampling_rate_hz']:g}Hz（header 提取）"
                  + (f"，探头 x{res['probe']:g}" if res["probe"] else ""))
        print(f"     幅值  Vmin={res['vmin']}V  Vmax={res['vmax']}V  Vpp={res['vpp']}V")
        print(f"     直流  Vmean={res['vmean']}V  Vrms={res['vrms']}V")
        if res["freq_hz"] is not None:
            print(f"     频率  {res['freq_hz']}Hz  (T={res['period_s']}s)  占空比 {res['duty_pct']}%")
        else:
            print("     频率  N/A（非周期性信号或点数不足）")
    except Exception as exc:  # noqa: BLE001
        print(f"❌ DS100 CSV 解析失败：{exc}")
        return 1
    return 0


def _print_ds100_plan(args) -> None:
    print("🔍 dry-run：DS100 CSV 解析流程（不读文件）\n")
    print(f"  读 {args.parse_ds100} → 跳过 header → 探测列 + 提取采样率")
    print(f"    time_col={args.time_col or 'auto'}  volt_col={args.volt_col or 'auto'}"
          f"  xinc={args.xinc or 'header→1e-6'}")
    print("  测量：Vmin/Vmax/Vpp/Vmean/Vrms + 过中值频率/周期/占空比")
    if args.save:
        print(f"  save_csv → {args.save}")
    if args.plot:
        print(f"  plot_waveform → {args.plot}")


def _print_scpi_plan(args) -> None:
    """dry-run 必须打印**真实**会执行的序列。

    老版本这里打的是 :TRIG:MOD SING / :SINGle / *OPC?，而 do_capture 实际
    走的是 :RUN + 等待——dry-run 和真跑不一致，等于骗人。
    """
    print("🔍 dry-run：将执行以下 SCPI 命令序列（未连接设备）\n")
    print(f"  rm.list_resources() → 挑 RIGOL（--resource {args.resource or 'auto'}）")
    print("  *IDN?   → 识别型号")
    if args.autoset:
        print("  :AUT    → 自动选档 + *OPC? 等待完成（本机实测 6.9s）")
    if args.timebase is not None:
        print(f"  :TIM:SCAL {args.timebase:g}  →  :TIM:SCAL?")
    if args.coupling:
        print(f"  :{args.channel}:COUP {args.coupling}  →  :{args.channel}:COUP?")
    if args.vdiv is None:
        print(f"  :{args.channel}:SCAL?  → 读当前灵敏度（未指定 --vdiv）")
    else:
        print(f"  :{args.channel}:SCAL {args.vdiv:g}")
    if args.voffs is not None:
        print(f"  :{args.channel}:OFFS {args.voffs:g}")
    print(f"  :{args.channel}:OFFS?  →  回读实际生效的偏置")
    print(f"  :WAV:SOUR {args.channel} / :WAV:FORM BYTE / :WAV:MODE {args.mode}")
    if args.mode.upper() == "RAW":
        depth = args.points or 12000
        print(f"  :ACQ:MDEP {depth} / :WAV:STAR 1 / :WAV:STOP {depth}")
    print(f"  :RUN    → 连续采集（等 {args.settle}s）")
    print("  :WAV:YREF? / :WAV:YINC? / :WAV:YOR?  → 前导，用于 V=(code-YREF-YOR)*YINC")
    print("  :WAV:XINC? / :WAV:XOR?  → 时间轴")
    print("  :WAV:POIN?（NORM 下）→ 横向格数=点数/100（DS1074Z 12 格 / DS2302A 14 格）")
    print("  :WAV:DATA?  → read_raw 取 TMC 块 → parse_tmc_block → 还原电压")
    print("  analyze_samples → 整数周期频率/占空比 + 削顶/平线/噪声判定")
    if args.measure:
        for item in [i.strip().lower() for i in args.measure.split(",") if i.strip()]:
            scpi = MEASURE_ITEMS.get(item, item.upper())
            print(f"  :MEAS:ITEM? {scpi},{args.channel}（DS1000Z）或 "
                  f":MEAS:{scpi}? {args.channel}（DS2000A）——形式自动探测，重试 3 次")
    if args.save:
        print(f"  save_csv → {args.save}")
    if args.plot:
        print(f"  plot_waveform → {args.plot}")
    print("\n  注：抓取走 :RUN 而非 :SINGle——:SINGle 未触发成功时"
          ":WAV:DATA? 回空块 #0（实测复现）")


def _print_measure_plan(args) -> None:
    print("🔍 dry-run：将执行以下 SCPI 命令序列（未连接设备）\n")
    print("  *IDN?  → 识别型号")
    print("  :RUN   → 让测量引擎有实时数据（:STOP 状态下部分项只回 9.9E37 哨兵）")
    print("  形式探测：按 *IDN? 猜一种，再用 600ms 短超时发一次 :MEAS:VPP? 确认，"
          "不对就换另一种")
    for item in [i.strip() for i in args.measure.split(",") if i.strip()]:
        scpi = MEASURE_ITEMS.get(item, item.upper())
        print(f"  :MEAS:ITEM? {scpi},{args.channel}（DS1000Z）或 "
              f":MEAS:{scpi}? {args.channel}（DS2000A）"
              "  （超时重试 3 次；9.9E37 判为无有效测量）")


class _Parser(argparse.ArgumentParser):
    """用法错误统一退出码 1。

    argparse 默认用 2，但本脚本把 2 定义为"取到数据但不可信"。若用法错误也返回
    2，按 rc 分支的调用方会把一个拼错的参数误判成"连上仪器并测到值了"。
    """

    def error(self, message: str):  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"❌ 参数错误：{message}", file=sys.stderr)
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        description="Rigol 示波器抓取/测量（/ea scope）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  py scope.py --detect\n"
            "  py scope.py --list\n"
            "  py scope.py --capture --channel CHAN1 --save wave.csv --plot wave.png\n"
            "  py scope.py --capture --coupling DC --vdiv 1 --timebase 5e-5 --measure vpp,freq,pduty\n"
            "  py scope.py --measure vpp,freq,period\n"
            "  py scope.py --parse-tmc raw.bin --vdiv 1.0 --save out.csv --plot out.png\n"
            "  py scope.py --resource USB0::0x1AB1::0x04CE::DS1ZA000000::INSTR --capture\n"
            "\n"
            "正点原子 DS100（U 盘导出 CSV）：\n"
            "  py scope.py --parse-ds100 E:/DS100_DATA/latest.csv --plot ds100.png\n"
            "  py scope.py --parse-ds100 wave.csv --sample-rate 1000000 --time-col 0 --volt-col 1\n"
            "\n"
            "退出码: 0=正常 / 1=失败 / 2=取到数据但不可信（削顶、平线、有项目测不出）\n"
        ),
    )
    act = parser.add_mutually_exclusive_group()
    act.add_argument("--detect", action="store_true", help="探测后端/资源/设备")
    act.add_argument("--list", action="store_true", help="列出所有 VISA 资源")
    act.add_argument("--capture", action="store_true",
                     help="抓波形（默认主流程；可与 --measure 同时给，同一连接里既抓又测）")
    act.add_argument("--parse-tmc", metavar="FILE", help="解析原始 TMC 块文件（自测）")
    act.add_argument("--parse-ds100", metavar="FILE.csv",
                     help="解析正点原子 DS100 导出波形 CSV（跳过 header，自动探测列）")
    parser.add_argument("--measure", metavar="VPP,FREQ,...",
                        help="SCPI 内置测量；单独给=只测量，与 --capture 一起给=同一连接内"
                             "既抓波形又测量（原先二者互斥，文档里的闭环示例根本跑不了）")

    parser.add_argument("--resource", help="VISA 资源串（缺省自动挑 RIGOL）")
    parser.add_argument("--backend",
                        help="VISA 后端：留空=系统 VISA 优先（USB-TMC 必需，无资源再回退 "
                             "@py）/ @py 强制纯 Python（网口）/ ni 显式 NI-VISA")
    parser.add_argument("--timeout", type=int, default=3000,
                        help="查询超时 ms（默认 3000；两族机型对不认识的助记符都不回复，"
                             "超时太短会把机型差异误报成链路故障）")
    parser.add_argument("--chunk", type=int, default=32, help="read_raw 分块字节（默认 32）")
    parser.add_argument("--settle", type=float, default=0.8,
                        help=":RUN 后等波形就绪的秒数（默认 0.8；改时基/档位后太短会抓到旧帧）")

    parser.add_argument("--channel", default="CHAN1", help="通道（默认 CHAN1）")
    parser.add_argument("--vdiv", type=float, help="垂直灵敏度 V/div（默认读 SCPI）")
    parser.add_argument("--voffs", type=float,
                        help="通道偏置 V，会写进仪器 :{ch}:OFFS（缺省不动仪器当前值）")
    parser.add_argument("--timebase", type=float, help="时基 s/div，写 :TIM:SCAL")
    parser.add_argument("--coupling", choices=["DC", "AC", "GND"],
                        help="通道耦合。测电压/占空比必须 DC——出厂默认常是 AC，"
                             "AC 会把直流分量和低频占空比一起吃掉")
    parser.add_argument("--autoset", action="store_true",
                        help="先发 :AUT 让仪器自动选档（实测约 6.9s），再抓取")
    parser.add_argument("--points", type=int,
                        help="采样深度 :ACQ:MDEP（仅 --mode RAW 有效；NORM 只回屏幕点）")
    parser.add_argument("--mode", choices=["NORM", "RAW"], default="NORM",
                        help=":WAV:MODE，NORM 屏幕点（默认） / RAW 深内存（需 --points 定区间，"
                             "读取前会自动 :STOP，部分机型 :ACQ:MDEP 不可写）")
    parser.add_argument("--xinc", type=float, help="每采样点时间间隔 s（--parse-tmc / DS100 单列用，默认 1us）")
    parser.add_argument("--sample-rate", type=float,
                        help="采样率 Hz（DS100 便捷写法，等价 xinc=1/rate；与 --xinc 冲突）")
    parser.add_argument("--time-col", type=int, help="DS100 CSV 时间列（0 基，默认自动探测）")
    parser.add_argument("--volt-col", type=int, help="DS100 CSV 电压列（0 基，默认自动探测）")

    parser.add_argument("--save", metavar="FILE.csv", help="保存波形 CSV")
    parser.add_argument("--plot", metavar="FILE.png", help="出波形图 png")

    add_common_args(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # --sample-rate 便捷写法 → xinc（互斥校验）
    if args.sample_rate is not None:
        if args.xinc is not None:
            print("❌ --sample-rate 与 --xinc 互斥，只给一个")
            return 1
        args.xinc = 1.0 / args.sample_rate
    if args.measure and (args.parse_tmc or args.parse_ds100):
        print("❌ --measure 只对实时仪器有效，不能与 --parse-tmc/--parse-ds100 同用")
        return 1
    if args.detect:
        return do_detect(args)
    if args.list:
        return do_list(args)
    if args.parse_tmc:
        return do_parse_tmc(args)
    if args.parse_ds100:
        return do_parse_ds100(args)
    # --measure 单独给 → 只测量；与 --capture 一起给 → 同一连接里既抓又测
    if args.measure and not args.capture:
        return do_measure(args)
    return do_capture(args)


def probe_idn(resources: list[str] | None) -> list[dict]:
    """对每个资源试 *IDN?，把"插上了"和"能通上话"区分开。

    命令文档声称 --detect 会做 *IDN?，老实现只列资源、不查 IDN。
    """
    out: list[dict] = []
    if not resources:
        return out
    try:
        import pyvisa

        rm = pyvisa.ResourceManager()
    except Exception:  # noqa: BLE001
        return out
    for res in resources:
        item = {"resource": res, "idn": None, "error": None}
        try:
            inst = rm.open_resource(res)
            try:
                inst.timeout = 3000
            except Exception:  # noqa: BLE001
                pass
            item["idn"] = inst.query("*IDN?").strip()
            inst.close()
        except Exception as exc:  # noqa: BLE001 - 探测阶段任何异常都算不可达
            item["error"] = str(exc)
        out.append(item)
    return out


def do_detect(args) -> int:
    import deps_check

    d = deps_check.detect()
    report = {
        "pyvisa": d["pip"].get("pyvisa"),
        "pyvisa_py": d["pip"].get("pyvisa-py"),
        "numpy": d["pip"].get("numpy"),
        "matplotlib": d["pip"].get("matplotlib"),
        "scope_ready": d["scope_ready"],
        "visa_resources": d["visa_resources"],
    }
    if report["visa_resources"]:
        report["idn"] = probe_idn(report["visa_resources"])
    if args.json:
        emit_json(report)
        return 0

    print("🔬 /ea scope 依赖探测\n")
    for key, label in (("pyvisa", "pyvisa"), ("pyvisa_py", "pyvisa-py"),
                       ("numpy", "numpy"), ("matplotlib", "matplotlib")):
        print(f"  {'✅' if report[key] else '⬜'} pip: {label}")
    res = report["visa_resources"]
    if res is None:
        print("  ⬜ VISA 资源: pyvisa 未装")
    elif not res:
        print("  ⬜ VISA 资源: 无设备（示波器未插 / 未装 USB-TMC 驱动，可用 Zadig 装 WinUSB）")
    else:
        print(f"  ✅ VISA 资源: {len(res)} 个")
        for item in report.get("idn", []):
            if item["idn"]:
                print(f"      {item['resource']}\n        → {item['idn']}")
            else:
                print(f"      {item['resource']}\n        → ⚠️ *IDN? 无响应："
                      f"{(item['error'] or '')[:80]}")
    print(f"\n  {'✅ /ea scope 就绪' if report['scope_ready'] else '⬜ /ea scope 缺依赖'}")
    return 0 if report["scope_ready"] else 1


def do_list(args) -> int:
    ns = require_module("import pyvisa", "pyvisa", "pyvisa")
    if ns is None:
        return 1
    rm = ensure_pyvisa(args, ns)
    resources = list_resources(rm)
    if args.json:
        emit_json({"resources": resources})
        return 0
    if not resources:
        print("（无 VISA 资源：示波器未插 / 未装驱动 / 后端缺失）")
        return 0
    for res in resources:
        kind = "USB" if "USB" in res else ("TCPIP" if "TCPIP" in res else "OTHER")
        print(f"  [{kind}] {res}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
