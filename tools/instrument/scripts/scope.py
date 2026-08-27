#!/usr/bin/env python
"""Rigol 示波器抓取/测量工具 + 正点原子 DS100 CSV 解析（/ea scope）。

两条路径：

A. Rigol 实时 SCPI（PyVISA + SCPI over USB/LAN，DS1000Z 系列）：
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

# 测量项名映射（DS1000Z SCPI 名 → 展示名）
MEASURE_ITEMS = {
    "vmax": "VMAX", "vmin": "VMIN", "vpp": "VPP", "vtop": "VTOP",
    "vbase": "VBASe", "vamp": "VAMP", "vrms": "VRMS", "vavg": "VAVG",
    "freq": "FREQ", "period": "PERIod", "rise": "RISE", "fall": "FALL",
    "pwidth": "PWIDth", "nwidth": "NWIDth", "pduty": "PDUTy", "nduty": "NDUTy",
}

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


def configure_channel(inst, ch: str, vdiv: float | None) -> tuple[float, float]:
    """读通道垂直灵敏度/偏置（未显式给则查 SCPI）。返回 (vdiv, voffs)。"""
    if vdiv is None:
        vdiv = float(inst.query(f":{ch}:SCAL?"))
    voffs = float(inst.query(f":{ch}:OFFS?"))
    return vdiv, voffs


def setup_acquisition(inst, ch: str, points: int | None, mode: str) -> None:
    """WAVeform 配置：数据源 / 编码 / 模式 / 深度。"""
    inst.write(":WAV:SOUR " + ch)
    inst.write(":WAV:FORM BYTE")
    inst.write(":WAV:MODE " + mode)  # NORM(屏幕) / RAW(深内存)
    if points:
        inst.write(f":ACQ:MDEP {points}")


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


def parse_tmc_block(raw: bytes) -> bytes:
    """解析 TMC 二进制块 #NXXXXXX<data>：N 位 ASCII 长度 + 精确切片。"""
    if not raw or raw[:1] != TMC_HEADER:
        raise ValueError(f"TMC 块头错误：期望 '#'，实际 {raw[:4]!r}")
    n = int(chr(raw[1]))  # 长度字段位数
    if 2 + n > len(raw):
        raise ValueError(f"TMC 长度头截断：n={n}，raw 长度 {len(raw)}")
    length = int(raw[2:2 + n].decode("ascii"))
    data = raw[2 + n:2 + n + length]
    if len(data) < length:
        raise ValueError(f"TMC 数据截断：声明 {length}，实际 {len(data)}")
    return data


def decode_to_volts(payload: bytes, vdiv: float, voffs: float) -> np.ndarray:
    """BYTE 格式还原电压：(val-128)/25*每格伏数 + 偏置。"""
    vals = np.frombuffer(payload, dtype=np.uint8)
    return (vals.astype(np.float64) - 128.0) / 25.0 * vdiv + voffs


def build_time_axis(inst, n: int) -> np.ndarray:
    """:WAV:XINC? 时间间隔，:WAV:XOR? 起始时刻（可能返回两值取第一个）。"""
    xinc = float(inst.query(":WAV:XINC?"))
    xorig_resp = inst.query(":WAV:XOR?").strip()
    xorig = float(xorig_resp.split(",")[0])
    return xorig + np.arange(n) * xinc


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


def do_capture(args) -> int:
    if args.dry_run:
        _print_scpi_plan(args)
        return 0

    ns = require_module("import pyvisa", "pyvisa", "pyvisa")
    if ns is None:
        return 1
    rm = ensure_pyvisa(args, ns)
    try:
        resource = find_rigol(rm, args.resource)
        inst = open_instrument(rm, resource, args)
        with inst:
            idn = identify(inst)
            print(f"  ▶ {idn}")
            vdiv, voffs = configure_channel(inst, args.channel, args.vdiv)
            setup_acquisition(inst, args.channel, args.points, args.mode)
            # 读取当前屏幕波形：用 :RUN 连续采集（数据总就绪）。
            # 勿用 single_shot（:SINGle 未触发成功时 :WAV:DATA? 返回空 `#9000000000`，实测复现）。
            inst.write(":RUN")
            time.sleep(0.4)  # 等一帧就绪
            raw = read_waveform(inst)
            payload = parse_tmc_block(raw)
            volts = decode_to_volts(payload, vdiv, voffs)
            times = build_time_axis(inst, len(volts))

            if args.save:
                save_csv(times, volts, args.save)
            if args.plot:
                plot_waveform(times, volts, args.plot, args.channel)

            result = {
                "idn": idn, "channel": args.channel, "points": len(volts),
                "vdiv": vdiv, "voffs": voffs, "duration_s": float(times[-1] - times[0]),
                "min_v": round(float(volts.min()), 6),
                "max_v": round(float(volts.max()), 6),
                "vpp_est": round(float(volts.max() - volts.min()), 6),
            }
            if args.json:
                emit_json(result)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 抓取失败：{exc}")
        return 1
    return 0


def do_measure(args) -> int:
    if args.dry_run:
        _print_measure_plan(args)
        return 0

    ns = require_module("import pyvisa", "pyvisa", "pyvisa")
    if ns is None:
        return 1
    rm = ensure_pyvisa(args, ns)
    items = [i.strip().lower() for i in args.measure.split(",") if i.strip()]
    unknown = [i for i in items if i not in MEASURE_ITEMS]
    if unknown:
        print(f"❌ 未知测量项 {unknown}（支持：{', '.join(MEASURE_ITEMS)}）")
        return 1
    try:
        resource = find_rigol(rm, args.resource)
        inst = open_instrument(rm, resource, args)
        with inst:
            idn = identify(inst)
            # 测量走连续采集：:RUN 保证测量引擎有实时数据。
            # 勿用 single_shot（:SINGle 后 ARMED 无触发则 :MEAS:ITEM? 返回 9.9e37 哨兵）。
            inst.write(":RUN")
            time.sleep(0.5)  # 等测量引擎刷新一帧
            values: dict[str, float | None] = {}
            for item in items:
                scpi = MEASURE_ITEMS[item]
                try:
                    inst.write(f":MEAS:ITEM {scpi},{args.channel}")
                    resp = inst.query(f":MEAS:ITEM? {scpi},{args.channel}").strip()
                    values[item] = float(resp)
                except (ValueError, Exception) as exc:  # noqa: BLE001
                    values[item] = None
                    print(f"  ⚠️  测量 {item} 失败：{exc}")
            result = {"idn": idn, "channel": args.channel, "measurements": values}
            if args.json:
                emit_json(result)
            else:
                print(f"  ▶ {idn} | {args.channel}")
                for k, v in values.items():
                    print(f"  {k:8s} = {v if v is not None else 'N/A'}")
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 测量失败：{exc}")
        return 1
    return 0


def do_parse_tmc(args) -> int:
    """无硬件自测：解析合成/保存的原始 TMC 块文件。"""
    raw = Path(args.parse_tmc).read_bytes()
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

    print(f"  ✅ TMC 解析成功: 头 {len(raw)}B → 数据 {len(payload)} 点")
    if args.save:
        save_csv(times, volts, args.save)
    if args.plot:
        plot_waveform(times, volts, args.plot, "CHAN1")
    if args.json:
        emit_json({"bytes": len(raw), "points": len(payload), "vdiv": vdiv,
                   "voffs": voffs, "xinc": xinc})
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
        mid = float(volts.min() + vpp / 2.0)
        above = volts >= mid
        # 上升沿过中值 → 周期（多周期取中位数更稳）
        rise_idx = np.where(np.diff(above.astype(np.int8)) > 0)[0]
        if len(rise_idx) >= 2:
            periods = np.diff(times[rise_idx])
            period = float(np.median(periods))
            if period > 0:
                res["period_s"] = round(period, 9)
                res["freq_hz"] = round(1.0 / period, 3)
        res["duty_pct"] = round(float(np.mean(above)) * 100.0, 2)
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
    print("🔍 dry-run：将执行以下 SCPI 命令序列（未连接设备）\n")
    print(f"  rm.list_resources() → 挑 RIGOL（--resource {args.resource or 'auto'}）")
    print(f"  *IDN?   → 识别型号")
    print(f"  :{args.channel}:SCAL? / :{args.channel}:OFFS?   → 读灵敏度/偏置")
    print(f"  :WAV:SOUR {args.channel} / :WAV:FORM BYTE / :WAV:MODE {args.mode}")
    if args.points:
        print(f"  :ACQ:MDEP {args.points}")
    print("  :ACQ:MODE NORM / :TRIG:MOD SING / :SINGle / *OPC?")
    print("  :WAV:DATA?  → read_raw 取 TMC 块 → parse_tmc_block → 还原电压/时间轴")
    if args.save:
        print(f"  save_csv → {args.save}")
    if args.plot:
        print(f"  plot_waveform → {args.plot}")


def _print_measure_plan(args) -> None:
    print("🔍 dry-run：将执行以下 SCPI 命令序列（未连接设备）\n")
    print("  *IDN?  → 识别型号")
    print("  :TRIG:MOD SING / :SINGle / *OPC?")
    for item in [i.strip() for i in args.measure.split(",") if i.strip()]:
        scpi = MEASURE_ITEMS.get(item, item.upper())
        print(f"  :MEAS:ITEM {scpi},{args.channel}  →  :MEAS:ITEM? {scpi},{args.channel}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rigol 示波器抓取/测量（/ea scope）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  py scope.py --detect\n"
            "  py scope.py --list\n"
            "  py scope.py --capture --channel CHAN1 --save wave.csv --plot wave.png\n"
            "  py scope.py --measure vpp,freq,period\n"
            "  py scope.py --parse-tmc raw.bin --vdiv 1.0 --save out.csv --plot out.png\n"
            "  py scope.py --resource USB0::0x1AB1::0x04CE::DS1ZA000000::INSTR --capture\n"
            "\n"
            "正点原子 DS100（U 盘导出 CSV）：\n"
            "  py scope.py --parse-ds100 E:/DS100_DATA/latest.csv --plot ds100.png\n"
            "  py scope.py --parse-ds100 wave.csv --sample-rate 1000000 --time-col 0 --volt-col 1\n"
        ),
    )
    act = parser.add_mutually_exclusive_group()
    act.add_argument("--detect", action="store_true", help="探测后端/资源/设备")
    act.add_argument("--list", action="store_true", help="列出所有 VISA 资源")
    act.add_argument("--capture", action="store_true", help="抓波形（默认主流程）")
    act.add_argument("--measure", metavar="VPP,FREQ,...", help="直接 SCPI 测量")
    act.add_argument("--parse-tmc", metavar="FILE", help="解析原始 TMC 块文件（自测）")
    act.add_argument("--parse-ds100", metavar="FILE.csv",
                     help="解析正点原子 DS100 导出波形 CSV（跳过 header，自动探测列）")

    parser.add_argument("--resource", help="VISA 资源串（缺省自动挑 RIGOL）")
    parser.add_argument("--backend", help="VISA 后端：@py（默认）/ 系统 VISA 留空 / ni")
    parser.add_argument("--timeout", type=int, default=1500, help="查询超时 ms（默认 1500）")
    parser.add_argument("--chunk", type=int, default=32, help="read_raw 分块字节（默认 32）")

    parser.add_argument("--channel", default="CHAN1", help="通道（默认 CHAN1）")
    parser.add_argument("--vdiv", type=float, help="垂直灵敏度 V/div（默认读 SCPI）")
    parser.add_argument("--voffs", type=float, default=0.0, help="偏置 V（默认 0）")
    parser.add_argument("--timebase", type=float, help="时基 s/div（预留，未用）")
    parser.add_argument("--points", type=int, help="采样深度 :ACQ:MDEP")
    parser.add_argument("--mode", choices=["NORM", "RAW"], default="NORM",
                        help=":WAV:MODE，NORM 屏幕点（默认） / RAW 深内存")
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
            return 2
        args.xinc = 1.0 / args.sample_rate
    if args.detect:
        return do_detect(args)
    if args.list:
        return do_list(args)
    if args.measure:
        return do_measure(args)
    if args.parse_tmc:
        return do_parse_tmc(args)
    if args.parse_ds100:
        return do_parse_ds100(args)
    return do_capture(args)


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
        for r in res[:8]:
            print(f"      {r}")
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
