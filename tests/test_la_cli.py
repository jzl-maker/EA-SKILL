# -*- coding: utf-8 -*-
"""回归测试：/ea la 的 CLI 层（dry-run 通道显示 / 触发抓取 / 内置解码 / 有损告警）。

不需要 Logic 2、不需要硬件：把假的 saleae.automation 塞进 sys.modules，
让 require_module 的 `from saleae import automation` 拿到它。
"""
import builtins
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import time
import types
from contextlib import redirect_stdout
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[1])
SCRIPTS = os.path.join(REPO, "tools", "instrument", "scripts")
sys.path.insert(0, SCRIPTS)

TMP = Path(tempfile.mkdtemp(prefix="lacli_"))
results = []


def check(label, cond, detail=""):
    results.append(bool(cond))
    print(("[PASS] " if cond else "[FAIL] ") + label)
    if not cond:
        print("        " + str(detail))


# ───────────────────── 合成波形（假 capture 的产物） ─────────────────────

def gen_edges(baud, data, t0=0.0, bits=8, idle_bits=4.0):
    bit = 1.0 / baud
    edges = [(t0, 1)]
    t = t0 + idle_bits * bit
    for x in data:
        edges.append((t, 0))
        t += bit
        for k in range(bits):
            v = (x >> k) & 1
            if edges[-1][1] != v:
                edges.append((t, v))
            t += bit
        if edges[-1][1] != 1:
            edges.append((t, 1))
        t += bit
    return edges, t


def csv_text(ch_edges, t_end):
    chans = sorted(ch_edges)
    lines = ["Time [s]," + ",".join(f"Channel {c}" for c in chans)]
    ev = {}
    for c in chans:
        for t, v in ch_edges[c]:
            ev.setdefault(round(t, 12), {})[c] = v
    cur = {c: ch_edges[c][0][1] for c in chans}
    for t in sorted(ev):
        cur.update(ev[t])
        lines.append(f"{t:.12f}," + ",".join(str(cur[c]) for c in chans))
    lines.append(f"{t_end:.12f}," + ",".join(str(cur[c]) for c in chans))
    return "\n".join(lines) + "\n"


BAUD = 57600
PAYLOAD = bytes([0xEF, 0x01, 0x08, 0x82, 0xFF, 0xFF, 0x00, 0x2E])
e7, te7 = gen_edges(BAUD, PAYLOAD)
RAW_OK = csv_text({7: e7}, te7)

# 同一个字节流，但模拟 Logic 2 把不可打印字节渲染成 '.'
LOSSY = ("Time [s],data,error\n"
         + "".join(f"{i*0.0001:.4f},.,\n" for i in range(12))
         + "".join(f"{0.01+i*0.0001:.4f},A,\n" for i in range(3)))
CLEAN = ("Time [s],data,error\n"
         + "".join(f"{i*0.0001:.4f},AB,\n" for i in range(12)))

# 双通道波形：ch15 比 ch7 更早开口 —— 若把 ch7 标成 TX、ch15 标成 RX，应提示接反
_e15, _t15 = gen_edges(BAUD, b"\xAA\xAA", t0=0.0)
_e7, _t7 = gen_edges(BAUD, b"\xBB\xBB", t0=0.020)
RAW_PAIR = csv_text({7: _e7, 15: _e15}, max(_t7, _t15))

created = {}
HANG = {"on": False}
RAW_OVERRIDE = {"text": None}


class FakeCapture:
    def __init__(self, raw=RAW_OK, decoded=LOSSY):
        self.raw, self.decoded = raw, decoded
        self.analyzers, self.stopped, self.waited = [], False, False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def add_analyzer(self, atype, settings=None):
        self.analyzers.append((atype, settings))
        return len(self.analyzers)

    def wait(self):
        self.waited = True
        if HANG["on"]:
            time.sleep(3600)

    def stop(self):
        self.stopped = True

    def export_raw_data_csv(self, d):
        Path(d, "digital.csv").write_text(self.raw, encoding="utf-8")

    def export_data_table(self, p, analyzers=None):
        Path(p).write_text(self.decoded, encoding="utf-8")

    def save_capture(self, p):
        Path(p).write_text("sal", encoding="utf-8")


class FakeManager:
    def __init__(self, **kw):
        self.kw = kw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_devices(self, include_simulation_devices=False):
        return [types.SimpleNamespace(
            device_id="FAKE-1",
            device_type=types.SimpleNamespace(name="LOGIC_8"),
            is_simulation=False)]

    def start_capture(self, *, device_configuration, device_id=None,
                      capture_configuration=None):
        created["dev_cfg"] = device_configuration
        created["cap_cfg"] = capture_configuration
        cap = FakeCapture(raw=RAW_OVERRIDE["text"] or RAW_OK)
        created["capture"] = cap
        return cap

    def load_capture(self, path):
        created["loaded"] = path
        cap = FakeCapture(raw=RAW_OK, decoded=LOSSY)
        created["capture"] = cap
        return cap


class FakeLogicDeviceConfiguration:
    def __init__(self, **kw):
        self.kw = kw


class FakeCaptureConfiguration:
    def __init__(self, capture_mode=None):
        self.capture_mode = capture_mode


class FakeTimedCaptureMode:
    def __init__(self, duration_seconds=None, **kw):
        self.duration_seconds = duration_seconds


class FakeTriggerCaptureMode:
    def __init__(self, trigger_type=None, trigger_channel_index=None,
                 after_trigger_seconds=None, **kw):
        self.trigger_type = trigger_type
        self.trigger_channel_index = trigger_channel_index
        self.after_trigger_seconds = after_trigger_seconds


class FakeDigitalTriggerType:
    # 与已装 logic2-automation 一致：只有这四个成员（没有 DigitalTriggerEdge）
    FALLING, PULSE_HIGH, PULSE_LOW, RISING = "FALLING", "PULSE_HIGH", "PULSE_LOW", "RISING"


def install_fake_saleae():
    pkg = types.ModuleType("saleae")
    automation = types.ModuleType("saleae.automation")
    automation.Manager = types.SimpleNamespace(connect=lambda **kw: FakeManager(**kw))
    automation.LogicDeviceConfiguration = FakeLogicDeviceConfiguration
    automation.CaptureConfiguration = FakeCaptureConfiguration
    automation.TimedCaptureMode = FakeTimedCaptureMode
    automation.DigitalTriggerCaptureMode = FakeTriggerCaptureMode
    automation.DigitalTriggerType = FakeDigitalTriggerType
    pkg.automation = automation
    sys.modules["saleae"] = pkg
    sys.modules["saleae.automation"] = automation


install_fake_saleae()

spec = importlib.util.spec_from_file_location(
    "la", os.path.join(SCRIPTS, "logic_analyzer.py"))
la = importlib.util.module_from_spec(spec)
sys.modules["la"] = la
spec.loader.exec_module(la)


def run(argv, out_dir, hang=False):
    if out_dir.exists():
        shutil.rmtree(out_dir)
    created.clear()
    HANG["on"] = hang
    buf = io.StringIO()
    rc = None
    with redirect_stdout(buf):
        try:
            rc = la.main(["--export-dir", str(out_dir)] + argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, buf.getvalue()


# ─────────────────── 1. 问题 4：dry-run 必须显示真实通道 ───────────────────
out = TMP / "o1"
rc, txt = run(["--capture", "--channels", "7,15", "--dry-run"], out)
check("dry-run 显示真实通道 [7, 15]（不再是硬编码 0..7）",
      "enabled_digital_channels=[7, 15]" in txt, txt)
check("dry-run 不出现 0..7", "0..7" not in txt, txt)

rc, txt = run(["--capture", "--dry-run"], out)
check("未给 --channels 时 dry-run 显示默认 0..7 全展开",
      "enabled_digital_channels=[0, 1, 2, 3, 4, 5, 6, 7]" in txt, txt)

# ─────────────────── 2. 问题 3：触发抓取 ───────────────────
rc, txt = run(["--capture", "--channels", "7", "--duration", "0.2",
               "--trigger-channel", "7", "--trigger-edge", "falling", "--dry-run"], out)
check("dry-run 触发模式用 DigitalTriggerCaptureMode",
      "DigitalTriggerCaptureMode" in txt and "DigitalTriggerType.FALLING" in txt
      and "trigger_channel_index=7" in txt, txt)

rc, txt = run(["--capture", "--channels", "7", "--duration", "0.2",
               "--trigger-channel", "7", "--trigger-edge", "falling"], out)
cfg = created.get("cap_cfg")
mode = getattr(cfg, "capture_mode", None)
check("真实抓取时透传触发参数（通道/边沿/触发后时长）",
      isinstance(mode, FakeTriggerCaptureMode) and mode.trigger_channel_index == 7
      and mode.trigger_type == "FALLING" and mode.after_trigger_seconds == 0.2,
      repr(getattr(mode, "__dict__", mode)))
check("触发模式不再用 TimedCaptureMode",
      not isinstance(mode, FakeTimedCaptureMode), type(mode))

rc, txt = run(["--capture", "--channels", "7", "--duration", "0.02",
               "--trigger-channel", "7"], out)
check("无 --trigger-channel 时仍是 TimedCaptureMode",
      isinstance(run(["--capture", "--duration", "0.02"], TMP / "o2")[0], int)
      and isinstance(created.get("cap_cfg").capture_mode, FakeTimedCaptureMode),
      type(created.get("cap_cfg").capture_mode))

# 触发超时：wait() 永久阻塞时不能让工具挂死
t0 = time.time()
rc, txt = run(["--capture", "--channels", "7", "--duration", "0",
               "--trigger-channel", "7", "--trigger-timeout", "0.3"], out, hang=True)
elapsed = time.time() - t0
check(f"触发不来 → {elapsed:.1f}s 后中止并报错（不永久挂住）",
      rc == 1 and "没等到" in txt and elapsed < 15, f"rc={rc} elapsed={elapsed:.1f}\n{txt}")
check("中止时调用了 capture.stop()",
      created.get("capture") is not None and created["capture"].stopped, txt)

# ─────────────────── 3. 长录制提醒 ───────────────────
rc, txt = run(["--capture", "--channels", "7", "--duration", "120"], out)
check("无触发 + 长录制 → 提前提醒并指向 --trigger-channel",
      "无触发模式" in txt and "--trigger-channel" in txt, txt)
rc, txt = run(["--capture", "--channels", "7", "--duration", "0.05"], out)
check("短录制不刷提醒", "无触发模式" not in txt, txt)

# ─────────────────── 4. 问题 1：decoded.csv 有损告警 ───────────────────
rc, txt = run(["--capture", "--channels", "7", "--duration", "0.05",
               "--decoder", "uart:TX=7,Bit Rate (Bits/s)=57600"], out)
check("解码表有损 → 导出后立即告警",
      "decoded.csv" in txt and "不能当字节值用" in txt, txt)
check("告警指出退路（--export-raw + --decode-uart）",
      "--decode-uart" in txt and "digital.csv" in txt, txt)

rc, txt = run(["--load", str(TMP / "x.sal"), "--decoder", "uart:TX=7"], out)
check("--load 路径同样做有损扫描（原先那份复制粘贴的导出块）",
      "不能当字节值用" in txt, txt)

# ─────────────────── 5. 问题 2/5：内置解码 + 通道素描 ───────────────────
rc, txt = run(["--capture", "--channels", "7", "--duration", "0.05",
               "--decode-uart", f"ch=7,baud={BAUD}"], out)
check("--decode-uart 自动开启 --export-raw",
      "已自动开启 --export-raw" in txt and "export_raw_data_csv" in txt or "原始数据 CSV" in txt,
      txt)
check("内置解码还原出精确字节（含不可打印的 0x01/0x08/0x82）",
      "EF 01 08 82 FF FF 00 2E" in txt.replace("\n", " "), txt)
check("通道素描按 --export-raw 自动打印",
      "通道素描" in txt and "跳变" in txt and "波特率" in txt, txt)
check("通道素描给出波特率粗估 57600", "57600" in txt, txt)

# baud=auto 走最短脉宽粗估
rc, txt = run(["--capture", "--channels", "7", "--duration", "0.05",
               "--decode-uart", "ch=7,baud=auto"], out)
check("baud=auto 走粗估且字节正确",
      "EF 01 08 82 FF FF 00 2E" in txt.replace("\n", " "), txt)

# TX/RX 对：两路都解 + 接反提示（问题 5）
RAW_OVERRIDE["text"] = RAW_PAIR
rc, txt = run(["--capture", "--channels", "7,15", "--duration", "0.05",
               "--decode-uart", "TX=7,RX=15,baud=auto"], out)
RAW_OVERRIDE["text"] = None
check("TX/RX 对：两路都解出字节",
      "ch7 内置 UART 解码" in txt and "ch15 内置 UART 解码" in txt, txt)
check("RX 标注的通道先开口 → 端到端提示疑似 TX/RX 接反",
      "接反" in txt, txt)

# 通道号写错要报错，不能静默
rc, txt = run(["--capture", "--channels", "7", "--duration", "0.05",
               "--decode-uart", "ch=99,baud=9600"], out)
check("通道号不存在 → 明确报错并列出可用通道",
      "找不到通道" in txt and "ch7" in txt, txt)

# 波特率给错要报错，不能静默吐垃圾
rc, txt = run(["--capture", "--channels", "7", "--duration", "0.05",
               "--decode-uart", "ch=7,baud=9600"], out)
check("波特率错误 → 点明整份结果不可信（不静默吐垃圾字节）",
      "framing error" in txt and "整体不可信" in txt, txt[-700:])

# ─────────────────── 5b. --decode-spi / --decode-i2c ───────────────────

PERIOD = 1.0 / 1_000_000


def spi_csv(word, *, cpol=0, cpha=0, ch_clk=0, ch_mosi=1, ch_cs=3):
    ev = {ch_clk: [(0.0, cpol)], ch_mosi: [(0.0, 0)], ch_cs: [(0.0, 1)]}
    t = 10 * PERIOD
    ev[ch_cs].append((t, 0)); t += PERIOD
    for k in range(8):
        bit = (word >> (7 - k)) & 1
        ev[ch_mosi].append((t if cpha == 0 else t + PERIOD * 0.25, bit))
        ev[ch_clk].append((t + PERIOD * 0.25, 1 - cpol))
        ev[ch_clk].append((t + PERIOD * 0.75, cpol))
        t += PERIOD
    ev[ch_cs].append((t, 1))
    return csv_text(ev, t + PERIOD)


def i2c_csv(addr, *, ch_scl=4, ch_sda=5):
    ev = {ch_scl: [(0.0, 1)], ch_sda: [(0.0, 1)]}
    t = 10 * PERIOD

    def cycle(bit):
        nonlocal t
        ev[ch_sda].append((t, bit)); t += PERIOD * 0.25
        ev[ch_scl].append((t, 1)); t += PERIOD * 0.5
        ev[ch_scl].append((t, 0)); t += PERIOD * 0.25

    ev[ch_sda].append((t, 1)); t += PERIOD * 0.5
    ev[ch_scl].append((t, 1)); t += PERIOD * 0.5
    ev[ch_sda].append((t, 0)); t += PERIOD * 0.5          # START
    ev[ch_scl].append((t, 0)); t += PERIOD * 0.5
    for k in range(8):
        cycle((addr >> (7 - k)) & 1)
    cycle(0)                                              # ACK
    ev[ch_sda].append((t, 0)); t += PERIOD * 0.25
    ev[ch_scl].append((t, 1)); t += PERIOD * 0.5
    ev[ch_sda].append((t, 1))                             # STOP
    return csv_text(ev, t + PERIOD)


RAW_OVERRIDE["text"] = spi_csv(0x9F)
rc, txt = run(["--capture", "--channels", "0,1,3", "--duration", "0.05",
               "--decode-spi", "clk=0,mosi=1,cs=3,mode=0"], out)
check("--decode-spi 自动开启 --export-raw", "已自动开启 --export-raw" in txt, txt)
check("--decode-spi 解出精确字节 0x9F",
      "MOSI 字节流" in txt and "9F" in txt, txt)
check("--decode-spi 报告打印 mode 判定依据", "mode 判定" in txt, txt)
rc, txt = run(["--capture", "--channels", "0,1,3", "--duration", "0.05",
               "--decode-spi", "clk=99,mosi=1"], out)
check("--decode-spi 通道不存在 → 明确报错",
      "找不到通道" in txt and "ch0" in txt, txt)

RAW_OVERRIDE["text"] = i2c_csv(0xA0)
rc, txt = run(["--capture", "--channels", "4,5", "--duration", "0.05",
               "--decode-i2c", "scl=4,sda=5"], out)
check("--decode-i2c 识别 START / 地址 0x50 写 / ACK / STOP",
      "START" in txt and "STOP" in txt and "地址 0x50" in txt and "写" in txt, txt)
rc, txt = run(["--capture", "--channels", "4,5", "--duration", "0.05",
               "--decode-i2c", "4,5"], out)
check("--decode-i2c 位置形式 '4,5' 也可用", "地址 0x50" in txt, txt)
rc, txt = run(["--capture", "--channels", "4,5", "--duration", "0.05",
               "--decode-i2c", "scl=4"], out)
check("--decode-i2c 缺 sda → 报错不静默", "需要 scl 和 sda" in txt, txt)
RAW_OVERRIDE["text"] = None

# ─────────────────── 6. 产物行数/体积报告 ───────────────────
rc, txt = run(["--capture", "--channels", "7", "--duration", "0.05", "--export-raw"], out)
check("报告产物行数与体积", "产物" in txt and "digital.csv" in txt and "KB" in txt, txt)

# ─────────────────── 7. --load 忽略触发参数 ───────────────────
rc, txt = run(["--load", str(TMP / "x.sal"), "--trigger-channel", "7"], out)
check("--load 下触发参数被忽略并提示",
      "对 --load 无意义" in txt, txt)

# ─────────────────── 8. 回归：没给新参数时行为不变 ───────────────────
rc, txt = run(["--capture", "--channels", "0,1", "--duration", "0.05"], out)
check("回归：纯定时抓取仍正常返回", rc == 0 and isinstance(created.get("cap_cfg"), FakeCaptureConfiguration), txt)

print("─" * 50)
print(f"{sum(results)}/{len(results)} 通过")
sys.exit(0 if all(results) else 1)
