#!/usr/bin/env python
"""Saleae 逻辑分析仪抓取/解码工具（/ea la）。

基于官方 Logic 2 Automation API（logic2-automation），通过 gRPC 连接后台
Logic 2 软件的脚本服务器（默认端口 10430），支持：

- --detect         探测 pip 依赖 / Logic 2 软件 / 脚本端口 / 设备
- --list-devices   列出已连接设备（含模拟设备）
- --capture        时间触发抓取 + 协议解码 + 导出（默认主流程）
- --load <file.sal> 复用已有捕获做解码/导出（不重新抓取）
- --simulate       用 Logic 2 内置模拟设备（无硬件自测，解码管线照跑）
- --dry-run        只打印将执行的 API 调用序列，不连接任何设备

依赖：logic2-automation（pip）+ Logic 2 GUI 软件（非 pip，需单独安装）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import (
    add_common_args,
    check_logic2_port,
    default_output_dir,
    emit_json,
    find_logic2_exe,
    parse_value,
    require_module,
)

# 常用 analyzer 类型与设置键速查（以已装 Logic 2 的 Analyzer 定义为准）
ANALYZER_QUICK_REF = {
    "Async Serial": ["Input Channel", "Bit Rate (Bits/s)", "Bits per Frame",
                     "Stop Bits", "Parity Bit"],
    "SPI": ["CLK", "MOSI", "MISO", "Enable"],
    "I2C": ["SCL", "SDA"],
    "I2S / PCM": ["SCLK", "WS", "SD"],
    "CAN": ["CAN_TX", "CAN_RX", "Bit Rate (Bits/s)"],
    "1-Wire": ["Data"],
    "Manchester": ["Data"],
    "Modbus": ["UART TX", "UART RX"],
    "LIN": ["LIN", "Bit Rate (Bits/s)"],
    "SMBus": ["SMBCLK", "SMBDAT"],
    "MDIO": ["MDC", "MDIO"],
    "SWD": ["CLK", "SWDIO"],
}

# 别名 → Logic 2 真实类型名（add_analyzer 大小写敏感，必须精确）
ALIASES = {
    "uart": "Async Serial",
    "async serial": "Async Serial",
    "i2s": "I2S / PCM",
    "i2s / pcm": "I2S / PCM",
    "1-wire": "1-Wire",
    "one wire": "1-Wire",
}


def normalize_analyzer_type(atype: str) -> str:
    """把用户输入（别名/大小写不敏感）归一化为 add_analyzer 接受的精确类型名。"""
    key = atype.strip().lower()
    if key in ALIASES:
        return ALIASES[key]
    for real in ANALYZER_QUICK_REF:
        if real.lower() == key:
            return real
    return atype.strip()

# 各机型数字采样率上限（DeviceType 枚举名 → 上限，超出报错）
MAX_SAMPLE_RATE = {
    "LOGIC": 100_000_000,
    "LOGIC_4": 100_000_000,
    "LOGIC_8": 100_000_000,
    "LOGIC_16": 100_000_000,
    "LOGIC_PRO_8": 500_000_000,
    "LOGIC_PRO_16": 500_000_000,
}


def parse_decoder_specs(specs: list[str]) -> list[tuple[str, dict]]:
    """解析 --decoder "uart:TX=0,RX=1,Bit Rate (Bits/s)=115200"。

    返回 [(type大写, settings_dict)]，settings 值为 int/float/str 自动推断。
    """
    results: list[tuple[str, dict]] = []
    for spec in specs:
        type_part, _, rest = spec.partition(":")
        settings: dict = {}
        for pair in rest.split(","):
            pair = pair.strip()
            if not pair:
                continue
            key, _, value = pair.partition("=")
            key = key.strip()
            if not key:
                continue
            settings[key] = parse_value(value.strip())
        results.append((normalize_analyzer_type(type_part), settings))
    return results


def connect_manager(args, automation) -> object:
    """按参数建立 Manager：优先 connect 已运行实例，仅 --launch 且端口未开才启动。"""
    want_launch = args.launch and not check_logic2_port(args.port)
    if want_launch:
        exe = find_logic2_exe(args.logic2_path)
        if exe is None:
            print("❌ --launch 需要本机安装 Logic 2 软件（winget install Saleae.Logic2）")
            sys.exit(1)
        return automation.Manager.launch(application_path=exe)
    return automation.Manager.connect(port=args.port)


def pick_device(devices: list, name_filter: str | None, prefer_sim: bool = False) -> object:
    """选设备：--device 过滤；--simulate 优先模拟设备；否则第一个非模拟设备。"""
    if not devices:
        print("❌ 未发现设备。接入 Saleae 硬件，或使用 --simulate 模拟抓取。")
        sys.exit(1)
    if name_filter:
        for dev in devices:
            if name_filter.lower() in getattr(dev, "device_id", "").lower():
                return dev
        print(f"⚠️  未匹配 --device {name_filter}，使用第一个设备")
    if prefer_sim:
        for dev in devices:
            if getattr(dev, "is_simulation", False):
                return dev
    for dev in devices:
        if not getattr(dev, "is_simulation", False):
            return dev
    return devices[0]


def build_device_config(args, device, automation) -> object:
    """构造 LogicDeviceConfiguration（通道/采样率/阈值/模拟通道）。"""
    digital = list(range(8))
    if args.channels is not None:
        digital = [int(c) for c in args.channels.split(",") if c.strip()]

    rate = args.sample_rate
    dtype_name = getattr(getattr(device, "device_type", None), "name", "") or ""
    cap = MAX_SAMPLE_RATE.get(dtype_name, MAX_SAMPLE_RATE["LOGIC_8"])
    if rate > cap:
        print(f"❌ 采样率 {rate} 超出 {dtype_name or 'Logic8'} 上限 {cap}")
        sys.exit(1)

    kwargs: dict = {
        "enabled_digital_channels": digital,
        "digital_sample_rate": rate,
    }
    thr = getattr(args, "threshold", None)
    if thr is not None:
        if dtype_name in ("LOGIC_PRO_8", "LOGIC_PRO_16"):
            if thr not in (1.2, 1.8, 3.3):
                print(f"⚠️  阈值 {thr}V 不在 Pro 系列档位 (1.2/1.8/3.3V)，改用 1.8V")
                thr = 1.8
            kwargs["digital_threshold_volts"] = thr
        else:
            print(f"⚠️  {dtype_name or '该机型'} 不支持自定义阈值，忽略 --threshold {thr}V（用默认档位）")
    if args.analog_channels:
        kwargs["enabled_analog_channels"] = [int(c) for c in args.analog_channels.split(",")]
        kwargs["analog_sample_rate"] = args.analog_sample_rate
    return automation.LogicDeviceConfiguration(**kwargs)


def _expand_serial(settings: dict) -> list[dict]:
    """Async Serial 一个 analyzer 只含一个 Input Channel；TX/RX 拆成两个。"""
    common = {k: v for k, v in settings.items() if k not in ("TX", "RX")}
    out: list[dict] = []
    for key in ("TX", "RX"):
        if key in settings:
            s = dict(common)
            s["Input Channel"] = settings[key]
            out.append(s)
    if not out:
        out.append(dict(settings))
    return out


def add_analyzers(capture, specs: list[tuple[str, dict]]) -> list:
    """在 wait() 前添加 analyzer，返回 AnalyzerHandle 列表。"""
    ids: list = []
    for atype, settings in specs:
        real = normalize_analyzer_type(atype)
        if real == "Async Serial":
            for part in _expand_serial(settings):
                aid = capture.add_analyzer(real, settings=part)
                ids.append(aid)
                print(f"  ✅ analyzer: {real} {part}")
            continue
        aid = capture.add_analyzer(real, settings=settings)
        ids.append(aid)
        print(f"  ✅ analyzer: {real} {settings}")
    return ids


def export_capture(capture, args, out_dir: Path, analyzer_ids: list) -> None:
    """抓取完成后统一导出 raw CSV / 解码表 / .sal。

    注意：export_raw_data_csv / export_data_table / save_capture 的路径由 Logic 2
    后端进程解析（相对路径会落到其工作目录），这里一律转绝对路径；目录需先建好。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.export_raw:
        capture.export_raw_data_csv(str(out_dir))
        print(f"  ✅ 原始数据 CSV → {out_dir}/")
    if args.decoder:
        capture.export_data_table(str(out_dir / "decoded.csv"), analyzers=analyzer_ids)
        print(f"  ✅ 协议解码表 → {out_dir}/decoded.csv")
    if args.save:
        save = Path(args.save).resolve()
        capture.save_capture(str(save))
        print(f"  ✅ 捕获已保存 → {save}")


def plot_csv_dir(csv_dir: Path, png_path: str | None) -> None:
    """把 export_raw_data_csv 输出的 digital.csv 画成 step 波形图。

    digital.csv 是单文件：第一列时间，其余每列一个通道。
    """
    import matplotlib.pyplot as plt
    import numpy as np

    csv_file = csv_dir / "digital.csv"
    if not csv_file.is_file():
        print("⚠️  没有 digital.csv 可绘图（先 --export-raw）")
        return
    data = np.genfromtxt(csv_file, delimiter=",", names=True, dtype=float)
    names = data.dtype.names
    if not names or len(names) < 2:
        print("⚠️  digital.csv 没有通道列，跳过绘图")
        return
    t = data[names[0]]
    fig, ax = plt.subplots()
    for name in names[1:]:
        ax.step(t, data[name], where="post", label=name)
    ax.set_xlabel("time (s)")
    ax.set_ylabel("level")
    ax.legend()
    out = png_path or "la_waveform.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  ✅ 波形图 → {out}")


def do_detect(args) -> int:
    import deps_check

    d = deps_check.detect()
    report = {
        "logic2_automation": d["pip"].get("logic2-automation"),
        "numpy": d["pip"].get("numpy"),
        "matplotlib": d["pip"].get("matplotlib"),
        "la_ready": d["la_ready"],
        "logic2_exe": d["logic2_exe"],
        "logic2_port_open": d["logic2_port_open"],
    }
    if args.json:
        emit_json(report)
        return 0

    print("🔬 /ea la 依赖探测\n")
    for key, label in (("logic2_automation", "logic2-automation"),
                       ("numpy", "numpy"), ("matplotlib", "matplotlib")):
        print(f"  {'✅' if report[key] else '⬜'} pip: {label}")
    exe = report["logic2_exe"]
    if exe:
        print(f"  ✅ Logic 2 软件: {exe}")
        if report["logic2_port_open"]:
            print("  ✅ 脚本端口 10430: 已开启")
        else:
            print("  ⬜ 脚本端口 10430: 未开启（打开 Logic 2 → 设置 → 开启脚本服务器，或用 --launch）")
    else:
        print("  ⬜ Logic 2 软件: 未找到（winget install Saleae.Logic2）")
    print(f"\n  {'✅ /ea la 就绪' if report['la_ready'] else '⬜ /ea la 缺依赖'}")
    return 0 if report["la_ready"] else 1


def do_list_analyzer_types() -> int:
    print("常用 analyzer 类型与设置键（以已装 Logic 2 的 Analyzer 定义为准）:\n")
    for atype, keys in ANALYZER_QUICK_REF.items():
        print(f"  {atype}: {', '.join(keys)}")
    print("\n用法示例: --decoder \"uart:TX=0,RX=1,Bit Rate (Bits/s)=115200\"")
    return 0


def do_list_devices(args) -> int:
    ns = require_module("from saleae import automation", "logic2-automation",
                        "logic2-automation")
    if ns is None:
        return 1
    automation = ns["automation"]
    manager = connect_manager(args, automation)
    with manager:
        devices = manager.get_devices(include_simulation_devices=args.simulate)
        if args.json:
            emit_json({"devices": [{
                "device_id": getattr(d, "device_id", None),
                "device_type": getattr(getattr(d, "device_type", None), "name", None),
                "is_simulation": getattr(d, "is_simulation", None),
            } for d in devices]})
            return 0
        if not devices:
            print("（无设备）")
        for d in devices:
            sim = " [模拟]" if getattr(d, "is_simulation", False) else ""
            print(f"  {d.device_id}  "
                  f"[{getattr(getattr(d, 'device_type', None), 'name', '?')}]{sim}")
    return 0


def do_capture(args) -> int:
    specs = parse_decoder_specs(args.decoder)
    if args.dry_run:
        _print_dry_run(args, specs)
        return 0

    ns = require_module("from saleae import automation", "logic2-automation",
                        "logic2-automation")
    if ns is None:
        return 1
    automation = ns["automation"]

    manager = connect_manager(args, automation)
    with manager:
        devices = manager.get_devices(include_simulation_devices=args.simulate)
        device = pick_device(devices, args.device, prefer_sim=args.simulate)
        print(f"  ▶ 设备: {device.device_id} "
              f"[{getattr(getattr(device, 'device_type', None), 'name', '?')}]")

        dev_cfg = build_device_config(args, device, automation)
        capture = manager.start_capture(
            device_configuration=dev_cfg,
            capture_configuration=automation.CaptureConfiguration(
                capture_mode=automation.TimedCaptureMode(duration_seconds=args.duration)
            ),
        )
        with capture:
            analyzer_ids = add_analyzers(capture, specs)
            print(f"  ⏳ 抓取中 {args.duration}s ...")
            capture.wait()

            out_dir = (Path(args.export_dir) if args.export_dir
                       else default_output_dir("la")).resolve()
            export_capture(capture, args, out_dir, analyzer_ids)
            if args.plot and args.export_raw:
                plot_csv_dir(out_dir, args.plot)

            summary = {"device": getattr(device, "device_id", None),
                       "duration_s": args.duration, "export_dir": str(out_dir)}
            if args.json:
                emit_json(summary)
    return 0


def do_load(args) -> int:
    specs = parse_decoder_specs(args.decoder)
    if args.dry_run:
        _print_dry_run(args, specs, load=True)
        return 0

    ns = require_module("from saleae import automation", "logic2-automation",
                        "logic2-automation")
    if ns is None:
        return 1
    automation = ns["automation"]

    manager = connect_manager(args, automation)
    with manager:
        capture = manager.load_capture(str(args.load))
        with capture:
            analyzer_ids = add_analyzers(capture, specs)
            out_dir = (Path(args.export_dir) if args.export_dir
                       else default_output_dir("la_reuse")).resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
            if args.decoder:
                capture.export_data_table(str(out_dir / "decoded.csv"), analyzers=analyzer_ids)
                print(f"  ✅ 协议解码表 → {out_dir}/decoded.csv")
            if args.save:
                save = Path(args.save).resolve()
                capture.save_capture(str(save))
                print(f"  ✅ 捕获已保存 → {save}")
            if args.json:
                emit_json({"loaded": str(args.load), "export_dir": str(out_dir),
                           "analyzers": [s[0] for s in specs]})
    return 0


def _print_dry_run(args, specs: list[tuple[str, dict]], load: bool = False) -> None:
    print("🔍 dry-run：将执行以下 Automation API 调用序列（未连接任何设备）\n")
    port = check_logic2_port(args.port)
    exe = find_logic2_exe(args.logic2_path)
    print(f"  [连接] port={args.port} 端口{'已开' if port else '未开'} | "
          f"Logic 2={'找到' if exe else '未找到'}")
    if load:
        print(f"  manager.load_capture({args.load!r})")
    else:
        print("  manager.get_devices() → pick_device(...)")
        print(f"  LogicDeviceConfiguration(digital_channels=0..7, "
              f"sample_rate={args.sample_rate}, "
              f"threshold={args.threshold or '默认档位'})")
        print(f"  manager.start_capture(CaptureConfiguration("
              f"TimedCaptureMode({args.duration}s)))")
    for atype, settings in specs:
        print(f"  capture.add_analyzer({atype!r}, {settings!r})")
    print("  capture.wait()")
    if args.export_raw and not load:
        print("  capture.export_raw_data_csv(<export-dir>)")
    if args.decoder:
        print("  capture.export_data_table(<export-dir>, analyzers=[...])")
    if args.save:
        print(f"  capture.save_capture({args.save!r})")
    if args.plot:
        print(f"  plot_csv_dir(<export-dir>, {args.plot!r})")
    print("\n（用 --simulate 可无硬件真跑一遍解码管线）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Saleae 逻辑分析仪抓取/解码/导出（/ea la）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  py logic_analyzer.py --detect\n"
            "  py logic_analyzer.py --simulate --capture --duration 0.5 "
            "--decoder \"i2c:SCL=0,SDA=1\" --export-raw --plot la.png\n"
            "  py logic_analyzer.py --capture --channels 0,1,2,3 --sample-rate 10000000 "
            "--duration 2 --decoder \"uart:TX=0,RX=1,Bit Rate (Bits/s)=115200\"\n"
            "  py logic_analyzer.py --load trace.sal --decoder \"spi:CLK=0,MOSI=1,MISO=2\"\n"
        ),
    )
    act = parser.add_mutually_exclusive_group()
    act.add_argument("--detect", action="store_true", help="探测依赖/软件/端口/设备")
    act.add_argument("--list-devices", action="store_true", help="列出已连接设备")
    act.add_argument("--list-analyzer-types", action="store_true", help="列出常用解码器类型")
    act.add_argument("--capture", action="store_true", help="抓取（默认主流程）")
    act.add_argument("--load", metavar="FILE.sal", help="加载已有捕获做解码/导出")

    parser.add_argument("--channels", help="数字通道列表，如 '0,1,2,3'（默认 0..7）")
    parser.add_argument("--sample-rate", type=int, default=10_000_000, help="数字采样率 Hz（默认 10M）")
    parser.add_argument("--duration", type=float, default=1.0, help="抓取时长秒（默认 1.0）")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Pro 系列数字阈值档位 1.2/1.8/3.3 V（Logic8/16 自动忽略，用默认档位）")
    parser.add_argument("--analog-channels", help="模拟通道列表（Logic 8/Pro 8 支持）")
    parser.add_argument("--analog-sample-rate", type=int, default=10_000_000, help="模拟采样率")

    parser.add_argument("--decoder", action="append", default=[], metavar="TYPE:K=V,...",
                        help="添加解码器，可重复，如 'uart:TX=0,RX=1,Bit Rate (Bits/s)=115200'")

    parser.add_argument("--export-raw", action="store_true", help="导出原始数据 CSV（写目录）")
    parser.add_argument("--export-dir", help="导出目录（默认 <STATE_DIR>/captures/la_<ts>/）")
    parser.add_argument("--save", metavar="FILE.sal", help="保存 .sal 捕获文件")
    parser.add_argument("--plot", nargs="?", const="la_waveform.png", metavar="FILE.png",
                        help="出波形图 png（默认 la_waveform.png）")

    parser.add_argument("--port", type=int, default=10430, help="Logic 2 脚本端口（默认 10430）")
    parser.add_argument("--launch", action="store_true", help="Logic 2 未运行时自动启动")
    parser.add_argument("--simulate", action="store_true", help="用内置模拟设备（无硬件自测）")
    parser.add_argument("--logic2-path", help="显式指定 Logic 2 可执行文件")
    parser.add_argument("--device", help="设备名/ID 过滤（多设备时）")

    add_common_args(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.detect:
        return do_detect(args)
    if args.list_analyzer_types:
        return do_list_analyzer_types()
    if args.list_devices:
        return do_list_devices(args)
    if args.load:
        return do_load(args)
    return do_capture(args)


if __name__ == "__main__":
    sys.exit(main())
