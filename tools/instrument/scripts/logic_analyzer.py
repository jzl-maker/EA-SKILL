#!/usr/bin/env python
"""Saleae 逻辑分析仪抓取/解码工具（/ea la）。

基于官方 Logic 2 Automation API（logic2-automation），通过 gRPC 连接后台
Logic 2 软件的脚本服务器（默认端口 10430），支持：

- --detect         探测 pip 依赖 / Logic 2 软件 / 脚本端口 / 设备
- --list-devices   列出已连接设备（含模拟设备）
- --capture        定时抓取 + 协议解码 + 导出（默认主流程）
- --trigger-channel 数字边沿触发抓取（抓偶发事件，不必硬录满全程）
- --load <file.sal> 复用已有捕获做解码/导出（不重新抓取）
- --decode-uart    **不经 Logic 2 解码器**，从 digital.csv 的跳变时刻直接解出精确字节
- --decode-spi     同上，内置 SPI 解码（clk/mosi/miso/cs + mode）
- --decode-i2c     同上，内置 I2C 解码（START/STOP、7 位地址、ACK/NACK）
- --simulate       用 Logic 2 内置模拟设备（无硬件自测，解码管线照跑）
- --dry-run        只打印将执行的 API 调用序列，不连接任何设备

两条解码路径要分清：Logic 2 的 `export_data_table`（decoded.csv）方便看帧结构，但
**对二进制协议有损**（不可打印字节渲染成 `.`、NUL 渲染成 `\0`），字节值不可信；
`--export-raw` + `--decode-uart/--decode-spi/--decode-i2c` 走 `waveform.py`，只吃
digital.csv 的精确跳变时刻，是字节值的可靠来源。

依赖：logic2-automation（pip）+ Logic 2 GUI 软件（非 pip，需单独安装）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import waveform
from common import (
    add_common_args,
    check_logic2_port,
    default_output_dir,
    emit_json,
    find_logic2_exe,
    parse_value,
    require_module,
)

# --trigger-edge 取值 → logic2-automation 的 DigitalTriggerType 成员名。
# 三者必须精确匹配已装包（没有 DigitalTriggerEdge 这个枚举，别按直觉写）。
TRIGGER_TYPES = {
    "rising": "RISING",
    "falling": "FALLING",
    "pulse-high": "PULSE_HIGH",
    "pulse-low": "PULSE_LOW",
}

# 无触发模式下的长录制提醒阈值（秒）——偶发事件硬录很容易白录
LONG_CAPTURE_WARN_S = 30.0

# 触发等待兜底的宽限（秒）：留给 wait() 收尾与触发后录制的调度开销
TRIGGER_GRACE_S = 5.0

# 内置解码的错误帧占比超过它就认为整份结果不可信（波特率给错的典型表现是
# "零星伪字节 + 大量 framing error"，而不是全错，不点明就会被当成真数据）
UART_SUSPECT_ERROR_RATIO = 0.20

# 内置解码的三个开关（都在 waveform.py 里实现，都吃 digital.csv）
DECODE_FLAGS = ("decode_uart", "decode_spi", "decode_i2c")

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


def _channel_list(args) -> list[int]:
    """本次要采的数字通道。dry-run 与真实抓取共用，避免两处各写一份而对不上。"""
    if args.channels is not None:
        return [int(c) for c in args.channels.split(",") if c.strip()]
    return list(range(8))


def build_device_config(args, device, automation) -> object:
    """构造 LogicDeviceConfiguration（通道/采样率/阈值/模拟通道）。"""
    digital = _channel_list(args)

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


def build_capture_config(args, automation):
    """默认定时抓取；给了 --trigger-channel 则用数字边沿触发。

    两者互斥的字段要注意：DigitalTriggerCaptureMode **没有 duration_seconds**，
    触发后的录制长度由 after_trigger_seconds 决定（这里统一用 --duration 的值，
    含义从"录多久"变成"触发后录多久"）。
    """
    if args.trigger_channel is None:
        return automation.CaptureConfiguration(
            capture_mode=automation.TimedCaptureMode(duration_seconds=args.duration))

    member = TRIGGER_TYPES[args.trigger_edge]
    ttype = getattr(automation.DigitalTriggerType, member, None)
    if ttype is None:
        print(f"❌ 已装的 logic2-automation 没有 DigitalTriggerType.{member}，"
              f"请升级该 pip 包")
        sys.exit(1)
    print(f"  ▶ 触发: ch{args.trigger_channel} {args.trigger_edge} "
          f"（触发后录 {args.duration}s，最多等 {args.trigger_timeout or '∞'}s）")
    return automation.CaptureConfiguration(
        capture_mode=automation.DigitalTriggerCaptureMode(
            trigger_type=ttype,
            trigger_channel_index=args.trigger_channel,
            after_trigger_seconds=args.duration,
        ))


def warn_capture_size(args) -> None:
    """抓取前把"这次要录多久"摆到台面上 —— 偶发事件硬录很容易白录。"""
    if args.trigger_channel is not None or args.duration < LONG_CAPTURE_WARN_S:
        return
    samples = args.sample_rate * args.duration
    print(f"  ⚠️  无触发模式，将从头录满 {args.duration:g}s"
          f"（{samples/1e6:.0f}M 采样点/通道）")
    print("      偶发事件建议改用 --trigger-channel <n> --trigger-edge rising|falling，"
          "只录触发前后，避免白录")


def wait_capture(capture, args) -> bool:
    """等待抓取结束。返回 False 表示触发超时、本次没取到数据。

    触发模式下 `Capture.wait()` 会阻塞到触发发生为止，而 **API 没有超时参数** ——
    触发不来就是永久挂住。这里用守护线程做超时兜底，超时后 `capture.stop()` 中止。
    代价是 stop() 与 wait() 会并发（官方文档建议二者不要用于同一次抓取），这是
    为了"可中止"而接受的取舍；不想要这个取舍就用 --trigger-timeout 0 走原生无限等待。
    """
    if args.trigger_channel is None or args.trigger_timeout <= 0:
        capture.wait()
        return True

    import threading

    done = threading.Event()
    box: dict = {}

    def _worker():
        try:
            capture.wait()
            box["ok"] = True
        except Exception as exc:                       # noqa: BLE001 —— 原样抛回主线程
            box["err"] = exc
        finally:
            done.set()

    # wait() 包住"等触发 + 触发后录制"两段，而 API 不暴露触发时刻，
    # 所以兜底时长必须是 等触发上限 + 触发后录制时长，否则触发真来了也等不完。
    budget = args.trigger_timeout + args.duration + TRIGGER_GRACE_S
    threading.Thread(target=_worker, daemon=True).start()
    if not done.wait(budget):
        print(f"  ⏱️  等满 {args.trigger_timeout:g}s 也没等到 ch{args.trigger_channel} "
              f"{args.trigger_edge} 触发，中止本次抓取（未取到数据）")
        print("      确认触发条件（边沿方向/通道号）是否正确；"
              "确实需要无限等待就加 --trigger-timeout 0")
        try:
            capture.stop()
        except Exception as exc:                       # noqa: BLE001
            print(f"      （stop() 报告：{exc}）")
        return False
    if "err" in box:
        raise box["err"]
    return True


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


def export_decoded_table(capture, out_dir: Path, analyzer_ids: list) -> None:
    """导出协议解码表，并**立刻检查它是否被 Logic 2 渲染坏了**。

    Logic 2 把不可打印字节渲染成 `.`（0x2E）、NUL 渲染成字面量 `\\0`，二进制协议
    （UART/SPI/I2C）的数据列因此基本不可信 —— 真机据此得出过错误结论。导完就扫一遍，
    有问题当场说，别等用户把 `.` 当成真的 0x2E。
    """
    decoded = out_dir / "decoded.csv"
    capture.export_data_table(str(decoded), analyzers=analyzer_ids)
    print(f"  ✅ 协议解码表 → {decoded}")
    for line in waveform.format_lossy_warning(waveform.scan_decoded_csv(decoded)):
        print(line)


def export_raw_csv(capture, out_dir: Path) -> bool:
    """导出 digital.csv 等原始数据，返回是否成功（失败不阻断后续步骤）。"""
    try:
        capture.export_raw_data_csv(str(out_dir))
    except Exception as exc:                           # noqa: BLE001
        print(f"  ⚠️  原始数据导出失败：{exc}")
        return False
    print(f"  ✅ 原始数据 CSV → {out_dir}/digital.csv")
    return True


def run_waveform_analysis(args, out_dir: Path) -> None:
    """在 digital.csv 上做纯软件分析：通道素描 + 内置 UART 解码。

    这条路径完全绕开 Logic 2 的解码器，是解码表失真时的退路；通道素描也顺带回答
    "哪个通道才是 TX" 这个每次都要人肉数跳变的问题。
    """
    csv_path = out_dir / "digital.csv"
    if not csv_path.is_file():
        print(f"  ⚠️  没找到 {csv_path.name}，跳过通道统计/内置解码")
        return
    try:
        wave = waveform.load_digital_csv(csv_path)
    except (OSError, ValueError) as exc:
        print(f"  ⚠️  {csv_path.name} 解析失败：{exc}")
        return

    print(f"\n  📊 通道素描（{csv_path.name}，{wave.rows} 行，"
          f"{wave.duration:.6g}s）：")
    for ch in wave.channels:
        print(waveform.describe_channel(ch, wave.t_end))

    if not any(getattr(args, f) for f in DECODE_FLAGS):
        print("      提示：加 --decode-uart / --decode-spi / --decode-i2c \"...\" "
              "可直接解出精确字节（例：--decode-uart \"ch=7,baud=auto\"）")
        return

    results: dict[str, waveform.UartResult] = {}
    for spec_text in args.decode_uart:
        try:
            spec = waveform.parse_uart_spec(spec_text)
        except ValueError as exc:
            print(f"  ❌ --decode-uart {spec_text!r}: {exc}")
            continue
        for role, ch_spec in spec.channels.items():
            ch = _resolve_channel(wave, spec_text, "--decode-uart", ch_spec)
            if ch is None:
                continue
            baud = spec.baud
            if baud is None:
                est = ch.estimate_baud()
                if est is None:
                    print(f"  ❌ ch{ch.index} 没有跳变，无法估波特率，请显式给 baud=")
                    continue
                baud = est[1]
                if est[2] > 0.03:
                    print(f"  ⚠️  ch{ch.index} 波特率粗估 {est[0]:.0f} 距标准档 "
                          f"{est[1]} 偏 {est[2]*100:.1f}%，结果可能不可靠，"
                          f"建议显式给 baud=")
            res = waveform.decode_uart(ch, baud, data_bits=spec.data_bits,
                                       parity=spec.parity, stop_bits=spec.stop_bits)
            results[role.upper()] = res
            _print_uart_result(ch, res, spec)

    for spec_text in args.decode_spi:
        _run_spi_spec(wave, spec_text)
    for spec_text in args.decode_i2c:
        _run_i2c_spec(wave, spec_text)

    hint = waveform.swap_hint(results.get("TX"), results.get("RX"))
    if hint:
        print("  " + hint)


def _resolve_channel(wave, spec_text: str, flag: str, ch_spec: str):
    """通道号 → Channel；找不到就报错并列出可用通道（不静默跳过）。"""
    ch = wave.resolve(ch_spec)
    if ch is None:
        names = ", ".join(f"ch{c.index}" for c in wave.channels)
        print(f"  ❌ {flag} {spec_text!r}: 找不到通道 {ch_spec!r}（可用：{names}）")
    return ch


def _run_spi_spec(wave, spec_text: str) -> None:
    """解一路 SPI 配置（可含 MOSI/MISO 两向）。"""
    try:
        spec = waveform.parse_spi_spec(spec_text)
    except ValueError as exc:
        print(f"  ❌ --decode-spi {spec_text!r}: {exc}")
        return
    chans: dict = {}
    labels: dict[str, str] = {}
    for role, ch_spec in spec.signals.items():
        ch = _resolve_channel(wave, spec_text, "--decode-spi", ch_spec)
        if ch is None:
            return
        chans[role] = ch
        labels[role] = f"ch{ch.index}"
    res = waveform.decode_spi(chans["CLK"], chans.get("MOSI"), chans.get("MISO"),
                              chans.get("CS"), mode=spec.mode, bits=spec.bits,
                              order=spec.order)
    for line in waveform.format_spi_report(res, labels):
        print(line)


def _run_i2c_spec(wave, spec_text: str) -> None:
    """解一路 I2C 总线。"""
    try:
        spec = waveform.parse_i2c_spec(spec_text)
    except ValueError as exc:
        print(f"  ❌ --decode-i2c {spec_text!r}: {exc}")
        return
    scl = _resolve_channel(wave, spec_text, "--decode-i2c", spec.scl)
    sda = _resolve_channel(wave, spec_text, "--decode-i2c", spec.sda)
    if scl is None or sda is None:
        return
    res = waveform.decode_i2c(scl, sda)
    for line in waveform.format_i2c_report(res, f"ch{scl.index}", f"ch{sda.index}"):
        print(line)


def _print_uart_result(ch, res, spec) -> None:
    """打印一路 UART 的解码结果（精确字节 + 错误定位）。"""
    framing = sum(1 for f in res.errors if f.error == "framing")
    parity = sum(1 for f in res.errors if f.error == "parity")
    print(f"\n  🔎 ch{ch.index} 内置 UART 解码 "
          f"（{res.baud:g} baud {spec.data_bits}{spec.parity[:1].upper() or 'N'}"
          f"{spec.stop_bits:g}）：{len(res.good)} 字节"
          + (f"，framing error {framing}" if framing else "")
          + (f"，parity error {parity}" if parity else ""))
    if res.good:
        for line in waveform.format_hex_rows(res.bytes_):
            print(line)
    for f in res.errors[:5]:
        print(f"    ⚠️  t={f.t:.9f}s {f.error} error（值 0x{f.value:02X}，本帧不可用）")
    if len(res.errors) > 5:
        print(f"    ⚠️  另有 {len(res.errors)-5} 个错误帧未列出")
    total = len(res.frames)
    if total and len(res.errors) / total >= UART_SUSPECT_ERROR_RATIO:
        # 波特率给错时不是"全错"，而是解出零星几个伪字节 + 大量 framing error；
        # 那几个伪字节看着像真数据，必须点明整份结果不可信（真机踩过）。
        print(f"    ⚠️  错误帧占 {len(res.errors)}/{total}"
              f"（{len(res.errors)/total:.0%}）—— 整体不可信，别直接采信上面那几个字节。"
              f"优先核对 baud 是否对、该通道是否真是 UART"
              f"（波特率正确时应当基本没有 framing error）")


def report_artifacts(out_dir: Path) -> None:
    """报告实际产物的行数与体积 —— 抓完就该知道这次录了多少。"""
    files = sorted(p for p in out_dir.glob("*") if p.is_file())
    if not files:
        return
    print("\n  📦 产物：")
    for p in files:
        size = p.stat().st_size
        rows = ""
        if p.suffix == ".csv":
            try:
                with p.open("r", encoding="utf-8", errors="replace") as f:
                    rows = f"  {sum(1 for _ in f) - 1} 行"
            except OSError:
                pass
        print(f"     {p.name:<24} {size/1024:8.1f} KB{rows}")


def export_capture(capture, args, out_dir: Path, analyzer_ids: list) -> None:
    """抓取完成后统一导出 raw CSV / 解码表 / .sal。

    注意：export_raw_data_csv / export_data_table / save_capture 的路径由 Logic 2
    后端进程解析（相对路径会落到其工作目录），这里一律转绝对路径；目录需先建好。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.export_raw:
        export_raw_csv(capture, out_dir)
    if args.decoder:
        export_decoded_table(capture, out_dir, analyzer_ids)
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
        warn_capture_size(args)
        capture = manager.start_capture(
            device_configuration=dev_cfg,
            capture_configuration=build_capture_config(args, automation),
        )
        with capture:
            analyzer_ids = add_analyzers(capture, specs)
            print(f"  ⏳ 抓取中 {args.duration}s ..."
                  if args.trigger_channel is None
                  else f"  ⏳ 等待 ch{args.trigger_channel} {args.trigger_edge} 触发 ...")
            if not wait_capture(capture, args):
                return 1

            out_dir = (Path(args.export_dir) if args.export_dir
                       else default_output_dir("la")).resolve()
            export_capture(capture, args, out_dir, analyzer_ids)
            if args.export_raw:            # 有 digital.csv 就出通道素描（问题 5）
                run_waveform_analysis(args, out_dir)
            if args.plot and args.export_raw:
                plot_csv_dir(out_dir, args.plot)
            report_artifacts(out_dir)

            summary = {"device": getattr(device, "device_id", None),
                       "duration_s": args.duration, "export_dir": str(out_dir),
                       "trigger": (None if args.trigger_channel is None else
                                   {"channel": args.trigger_channel,
                                    "edge": args.trigger_edge})}
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
            if args.export_raw:
                export_raw_csv(capture, out_dir)
            if args.decoder:
                export_decoded_table(capture, out_dir, analyzer_ids)
            if args.save:
                save = Path(args.save).resolve()
                capture.save_capture(str(save))
                print(f"  ✅ 捕获已保存 → {save}")
            if args.export_raw:
                run_waveform_analysis(args, out_dir)
                if args.plot:
                    plot_csv_dir(out_dir, args.plot)
            report_artifacts(out_dir)
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
        chans = _channel_list(args)
        print("  manager.get_devices() → pick_device(...)")
        print(f"  LogicDeviceConfiguration(enabled_digital_channels={chans}, "
              f"digital_sample_rate={args.sample_rate}, "
              f"threshold={args.threshold or '默认档位'})")
        if args.trigger_channel is None:
            print("  manager.start_capture(CaptureConfiguration("
                  f"TimedCaptureMode(duration_seconds={args.duration})))")
        else:
            print("  manager.start_capture(CaptureConfiguration("
                  "DigitalTriggerCaptureMode("
                  f"trigger_type=DigitalTriggerType.{TRIGGER_TYPES[args.trigger_edge]}, "
                  f"trigger_channel_index={args.trigger_channel}, "
                  f"after_trigger_seconds={args.duration})))")
            print(f"  # 等触发的上限 {args.trigger_timeout:g}s（不含触发后录制的 "
                  f"{args.duration:g}s）；--trigger-timeout 0 = 无限等待，"
                  "触发不来会一直挂住")
    for atype, settings in specs:
        print(f"  capture.add_analyzer({atype!r}, {settings!r})")
    print("  capture.wait()")
    if args.export_raw and not load:
        print("  capture.export_raw_data_csv(<export-dir>)   # → digital.csv")
    if args.decoder:
        print("  capture.export_data_table(<export-dir>, analyzers=[...])")
    if args.save:
        print(f"  capture.save_capture({args.save!r})")
    if args.plot:
        print(f"  plot_csv_dir(<export-dir>, {args.plot!r})")
    if args.export_raw or any(getattr(args, f) for f in DECODE_FLAGS):
        print("\n  # 之后在 digital.csv 上做纯软件分析（不经 Logic 2 解码器）：")
        print("  waveform.load_digital_csv(<export-dir>/digital.csv)")
        print("  → 每通道素描：跳变数 / 高位占比 / 脉宽众数(位宽) / 波特率粗估")
        for kind in ("uart", "spi", "i2c"):
            for spec_text in getattr(args, f"decode_{kind}"):
                print(f"  → waveform.decode_{kind}({spec_text!r})  # 用跳变时刻直接解字节")
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
            "  py logic_analyzer.py --capture --channels 7 --export-raw "
            "--decode-uart \"ch=7,baud=auto\"\n"
            "      # ↑ 不经 Logic 2 解码器，直接从跳变时刻解出精确字节\n"
            "  py logic_analyzer.py --load trace.sal "
            "--decode-spi \"clk=0,mosi=1,miso=2,cs=3,mode=0\"\n"
            "  py logic_analyzer.py --load trace.sal --decode-i2c \"scl=3,sda=4\"\n"
            "      # ↑ SPI / I2C 内置解码，同样绕开 Logic 2 的失真解码表\n"
            "  py logic_analyzer.py --capture --channels 7,15 --trigger-channel 7 "
            "--trigger-edge falling --duration 0.2\n"
            "      # ↑ 只录 ch7 下降沿之后 0.2s，抓偶发事件不必硬录满全程\n"
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

    parser.add_argument("--decode-uart", action="append", default=[], metavar="ch=N,baud=B",
                        help="不依赖 Logic 2 的内置 UART 解码，直接从 digital.csv 的跳变"
                             "时刻解出精确字节。可重复；baud 给 auto 则按脉宽众数粗估。"
                             "例：'ch=7,baud=57600'、'TX=7,RX=15,baud=auto'。"
                             "自动开启 --export-raw")
    parser.add_argument("--decode-spi", action="append", default=[], metavar="clk=N,...",
                        help="内置 SPI 解码（同样只吃 digital.csv）。可重复；至少要给 "
                             "clk 和 mosi/miso 之一，cs 可选（给了按片选切帧最可靠）。"
                             "例：'clk=0,mosi=1,miso=2,cs=3,mode=0,bits=8,order=msb'；"
                             "mode 不给则自检 CPOL（CPHA 按 0）。自动开启 --export-raw")
    parser.add_argument("--decode-i2c", action="append", default=[], metavar="scl=N,sda=N",
                        help="内置 I2C 解码（同样只吃 digital.csv）。可重复。"
                             "例：'scl=3,sda=4'。输出 START/STOP、7 位地址 + 读写位、"
                             "每字节 ACK/NACK。自动开启 --export-raw")
    parser.add_argument("--trigger-channel", type=int, metavar="N",
                        help="数字触发通道号：只从该通道满足触发条件时开始录，"
                             "抓偶发事件不必硬录满全程")
    parser.add_argument("--trigger-edge", choices=sorted(TRIGGER_TYPES), default="rising",
                        help="触发条件（默认 rising）")
    parser.add_argument("--trigger-timeout", type=float, default=120.0, metavar="S",
                        help="触发模式下**等触发的上限**秒数（默认 120，0=无限等待）；"
                             "不含触发后录制的那段。Logic 2 的 wait() 本身没有超时，"
                             "触发不来会永久挂住，这里用守护线程兜底中止")

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


def normalize_args(args) -> None:
    """内置解码都要 digital.csv，没显式开 --export-raw 就自动打开。"""
    used = [f for f in DECODE_FLAGS if getattr(args, f)]
    if used and not args.export_raw:
        args.export_raw = True
        flag = "--" + used[0].replace("_", "-")
        print(f"  ℹ️  {flag} 需要 digital.csv，已自动开启 --export-raw")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    normalize_args(args)
    if args.load and args.trigger_channel is not None:
        print("  ⚠️  --trigger-channel 对 --load 无意义（捕获已录好），本次忽略")
        args.trigger_channel = None
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
