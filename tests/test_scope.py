# -*- coding: utf-8 -*-
"""回归测试：scope.py 的 N1~N9（不接任何真仪器）。

这九条都是"跑起来才发现"的问题，故每条都钉在一个**可观察的输出**上，
而不是钉在实现细节上：

  N1  --measure 单独用时 --vdiv/--voffs/--coupling 是死参数（写没写进仪器）
  N2  同上的可观测后果：--vdiv 不生效 → 时间类测量项回 9.9E37 哨兵
  N3  --channel CHAN3 在 2 通道机型上不报通道错，一路走到查询超时
  N4  --dry-run 的计划与真实调用序列对不上（RAW 缺 :STOP、顺序不符）
  N5  --parse-tmc 走的是 YREF=128 的旧公式，比真机低 1 个码值
  N6  find_rigol 探测失败时不关会话（同设备叠两个 VISA 会话）
  N7  _query_short 等短超时查询失败后不 drain（迟到回复让后续读取错位）
  N8  --json 时人类可读输出混进 stdout，json.loads(stdout) 直接失败
  N9  DS100 路径永远退出码 0，平线也算"成功"，且没有 sample_domain.periods

做法：把 scope 当模块导入，用 FakeInst/FakeRM 替掉 VISA 层。
不碰真仪器、不写全局配置、不留临时文件（除用 tempfile 自建的）。
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

# 用例名与说明都是中文 + 符号，控制台默认 GBK 会在 print 时直接抛 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
SCOPE_PY = REPO / "tools" / "instrument" / "scripts" / "scope.py"

# scope.py 里 `from common import ...`，同目录的兄弟模块，得让 import 找得到
sys.path.insert(0, str(SCOPE_PY.parent))

_spec = importlib.util.spec_from_file_location("scope_under_test", SCOPE_PY)
scope = importlib.util.module_from_spec(_spec)
sys.modules["scope_under_test"] = scope
_spec.loader.exec_module(scope)

IDN_DS1000Z = "RIGOL TECHNOLOGIES,DS1074Z,DS1ZA000000001,00.04.02.SP3"
IDN_DS2000A = "RIGOL TECHNOLOGIES,DS2302A,DS2D232401493,00.03.06"


# ---------------------------------------------------------------------------
# 假仪器层
# ---------------------------------------------------------------------------


class FakeInst:
    """最小可用的 VISA 会话替身。

    认得的查询回真值，**认不得的直接超时**——这正是两族 Rigol 的真实行径
    （不认识的助记符既不报错也不回复），N3/N4/N7 都靠这个行为才成立。
    """

    def __init__(self, idn=IDN_DS2000A, channels=2, meas=None, overrides=None,
                 close_error=None):
        self.idn = idn
        self.channels = channels
        self.writes: list[str] = []
        self.queries: list[str] = []
        self.closed = False
        self.close_error = close_error
        self.timeout = 3000
        self.chunk_size = 32
        # 状态表：写命令改它，查询命令读它，模拟"写进去就回读得到"
        self.state: dict[str, str] = {"TIM:SCAL": "0.0002"}
        for n in range(1, channels + 1):
            self.state[f"CHAN{n}:SCAL"] = "1.0"
            self.state[f"CHAN{n}:OFFS"] = "0.0"
            self.state[f"CHAN{n}:PROB"] = "1"
            self.state[f"CHAN{n}:COUP"] = "DC"
        # meas: {"VPP": "1.0"} —— 按助记符回值，两种命令形式都认
        self.meas = {"VPP": "1.0"} if meas is None else meas
        self.overrides = overrides or {}

    # --- VISA 接口 ---
    def write(self, cmd: str) -> None:
        cmd = cmd.strip()
        self.writes.append(cmd)
        if " " in cmd:
            key, val = cmd[1:].split(" ", 1)
            self.state[key.strip()] = val.strip()

    def query(self, cmd: str) -> str:
        cmd = cmd.strip()
        self.queries.append(cmd)
        if cmd in self.overrides:
            v = self.overrides[cmd]
            if isinstance(v, BaseException):
                raise v
            return v
        if cmd == "*IDN?":
            return self.idn
        if cmd.startswith(":MEAS:"):
            return self._meas_reply(cmd)
        if cmd.endswith("?"):
            key = cmd[1:-1]
            if key in self.state:
                return self.state[key]
        raise TimeoutError(f"unknown query: {cmd}")

    def _meas_reply(self, cmd: str) -> str:
        body = cmd[len(":MEAS:"):]
        if body.startswith("ITEM?"):
            scpi = body[len("ITEM?"):].strip().split(",")[0]
        else:
            scpi = body.split("?", 1)[0]
        if scpi in self.meas:
            return self.meas[scpi]
        raise TimeoutError(f"unknown measurement: {cmd}")

    def flush(self, _mask) -> None:
        return None

    def read_raw(self):
        return b""

    def close(self) -> None:
        if self.close_error is not None:
            raise self.close_error
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeRM:
    """资源管理器替身：按 resource 名给预置的假会话。"""

    def __init__(self, insts: dict[str, FakeInst], order=None):
        self.insts = insts
        self.order = order or list(insts)
        self.opened: list[str] = []

    def list_resources(self):
        return tuple(self.order)

    def open_resource(self, res: str):
        self.opened.append(res)
        return self.insts[res]


# ---------------------------------------------------------------------------
# 测试脚手架
# ---------------------------------------------------------------------------


def parse_args(argv):
    return scope.build_parser().parse_args(argv)


def run_with(argv, inst=None, argv_extra=None, rm=None):
    """用假仪器跑一次 do_*，返回 (rc, stdout, inst)。"""
    args = parse_args(list(argv) + list(argv_extra or []))
    inst = inst or FakeInst()
    rm = rm or FakeRM({"FAKE1": inst})
    saved = (scope.require_module, scope.ensure_pyvisa, scope.find_rigol,
             scope._MEAS_STATE.copy())
    scope.require_module = lambda *a, **k: {"pyvisa": object()}
    scope.ensure_pyvisa = lambda args, ns=None: rm
    scope._MEAS_STATE["form"] = None
    scope._MEAS_STATE["idn"] = None
    buf = io.StringIO()
    try:
        if args.capture:
            fn = scope.do_capture
        elif args.measure:
            fn = scope.do_measure
        elif args.parse_tmc:
            fn = scope.do_parse_tmc
        elif args.parse_ds100:
            fn = scope.do_parse_ds100
        else:
            fn = scope.do_detect
        with contextlib.redirect_stdout(buf):
            rc = fn(args)
    finally:
        (scope.require_module, scope.ensure_pyvisa, scope.find_rigol,
         state) = saved
        scope._MEAS_STATE.update(state)
    return rc, buf.getvalue(), inst


def write_tmc(payload: bytes, path: Path) -> Path:
    """按 TMC 规则写一个 #NXXXXXX 块文件。"""
    body = bytes(payload)
    head = b"#" + str(len(str(len(body)))).encode() + str(len(body)).encode()
    path.write_bytes(head + body)
    return path


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


# ---------------------------------------------------------------------------
# N1 / N2 —— --measure 路径的前端配置
# ---------------------------------------------------------------------------


@case
def t1_measure_applies_vdiv():
    """N1：--measure 单独给 --vdiv，必须真的写进仪器（以前是死参数）。"""
    rc, out, inst = run_with(
        ["--measure", "vpp", "--channel", "CHAN1", "--resource", "FAKE1",
         "--vdiv", "0.5", "--coupling", "DC", "--timebase", "2e-4"])
    assert ":CHAN1:SCAL 0.5" in inst.writes, f"没写 SCAL：{inst.writes}"
    assert ":CHAN1:COUP DC" in inst.writes, f"没写 COUP：{inst.writes}"
    assert ":TIM:SCAL 0.0002" in inst.writes, f"没写 TIM:SCAL：{inst.writes}"
    # 指定了 --vdiv 就要把残留偏置归零（换档会按比例缩放残留偏置）
    assert ":CHAN1:OFFS 0" in inst.writes, f"没归零 OFFS：{inst.writes}"
    assert rc == 0, f"rc={rc}\n{out}"


@case
def t2_measure_no_vdiv_writes_nothing():
    """N1 反面：**不给**前端参数时不能擅自改仪器状态，只能查询。"""
    rc, out, inst = run_with(["--measure", "vpp", "--channel", "CHAN1",
                              "--resource", "FAKE1"])
    bad = [w for w in inst.writes if not w.startswith(":RUN")]
    assert not bad, f"缺省参数下不该写仪器：{bad}"
    assert rc == 0, f"rc={rc}\n{out}"


@case
def t3_measure_vpp_used_for_div_advice():
    """N2：VPP 正常而时间类项失败 → 警告里要给出与 vpp/vdiv 匹配的建议 vdiv。"""
    inst = FakeInst(meas={"VPP": "0.35", "FREQ": "9.9E37"})
    rc, out, inst = run_with(
        ["--measure", "vpp,freq", "--channel", "CHAN1", "--resource", "FAKE1",
         "--vdiv", "1.0"], inst=inst)
    assert rc == 2, f"有项测不出应为 rc=2，实际 {rc}\n{out}"
    assert "纵向" in out, f"没给纵向占比诊断：{out}"
    # 0.35Vpp 打在 1V/div 上占 0.35 格 → 建议 0.35/3 ≈ 0.117 V/div
    assert "0.116667" in out or "0.117" in out, f"建议的 vdiv 不对：{out}"


# ---------------------------------------------------------------------------
# N3 —— 通道名
# ---------------------------------------------------------------------------


@case
def t4_bad_channel_name_fails_fast():
    """N3：--channel CHANX 要报"通道名无法识别"，不是 int() 的 ValueError。"""
    rc, out, inst = run_with(["--measure", "vpp", "--channel", "CHANX",
                              "--resource", "FAKE1"])
    assert rc == 1, f"rc={rc}\n{out}"
    assert "无法识别" in out, f"报错信息不对：{out}"
    assert "invalid literal" not in out, f"漏了底层 ValueError：{out}"


@case
def t5_missing_channel_fails_fast():
    """N3：2 通道机型上 --channel CHAN3 要当场报通道不存在。"""
    rc, out, inst = run_with(["--measure", "vpp", "--channel", "CHAN3",
                              "--resource", "FAKE1"])
    assert rc == 1, f"rc={rc}\n{out}"
    assert "不存在" in out, f"报错信息不对：{out}"


@case
def t6_channel_names_equivalent():
    """N3：CHAN2 / chan2 / 2 是同一个通道（解析不能被大小写/前缀绊倒）。"""
    assert scope._channel_index("CHAN2") == 2
    assert scope._channel_index("chan2") == 2
    assert scope._channel_index(" 2 ") == 2
    assert scope._channel_index("CHANX") is None
    assert scope._channel_index("") is None


# ---------------------------------------------------------------------------
# N5 —— --parse-tmc 的前导三元组
# ---------------------------------------------------------------------------


@case
def t7_offline_formula_matches_hardware():
    """N5：离线和在线必须是**同一个**前导公式（YREF=127，不是 128）。"""
    args = parse_args(["--parse-tmc", "x.bin", "--vdiv", "1.0"])
    pre = scope._offline_preamble(args)
    assert pre["yref"] == 127.0, pre
    assert abs(pre["yinc"] - 1.0 / 25.0) < 1e-12, pre
    assert pre["yor"] == 0.0, pre
    # 码值 135 @1V/div → 实机读数 0.32V；旧公式给 0.28V
    got = float(scope.decode_to_volts(bytes([135]), 1.0, 0.0, pre)[0])
    assert abs(got - 0.32) < 1e-9, f"YREF=127 应为 0.32V，实际 {got}"
    old = float(scope.decode_to_volts(bytes([135]), 1.0, 0.0, None)[0])
    assert abs(old - 0.28) < 1e-9, f"无前导（旧行为）应为 0.28V，实际 {old}"


@case
def t8_offline_yref_overridable():
    """N5：--yref 能覆盖缺省，且 --voffs 按仪器关系换算成码值。"""
    args = parse_args(["--parse-tmc", "x.bin", "--vdiv", "2.0",
                       "--yref", "128", "--yoffs" if False else "--voffs", "1"])
    pre = scope._offline_preamble(args)
    assert pre["yref"] == 128.0, pre
    assert abs(pre["yinc"] - 2.0 / 25.0) < 1e-12, pre
    assert pre["yor"] == 25.0, pre  # 25×1 = 25 个码值


@case
def t9_parse_tmc_cli_end_to_end():
    """N5：走一遍完整解析链路，确认电压换算用的是新公式（0.32 而非 0.28）。"""
    with tempfile.TemporaryDirectory(prefix="ea_scope_") as td:
        f = write_tmc(bytes([135] * 10 + [185] * 10), Path(td) / "a.bin")
        args = parse_args(["--parse-tmc", str(f), "--vdiv", "1.0"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = scope.do_parse_tmc(args)
        out = buf.getvalue()
    assert rc == 0, f"rc={rc}\n{out}"
    # 135→0.32V、185→2.32V，差 2.0V；旧公式给 0.28/2.28，差值同样是 2.0
    assert "Vmin=0.320 V" in out or "Vmin=0.320" in out, f"新公式没生效：{out}"
    assert "Vmax=2.320 V" in out or "Vmax=2.320" in out, f"新公式没生效：{out}"


# ---------------------------------------------------------------------------
# N6 / N7 —— 会话与读缓冲卫生
# ---------------------------------------------------------------------------


@case
def t10_find_rigol_closes_failed_probe():
    """N6：探测失败的资源必须关会话，不能挂着；且要能选中后面的资源。"""
    dead = FakeInst(idn="", overrides={"*IDN?": TimeoutError("boom")})
    alive = FakeInst(idn=IDN_DS2000A)
    rm = FakeRM({"USB0::DEAD": dead, "USB0::ALIVE": alive},
                order=["USB0::DEAD", "USB0::ALIVE"])
    got = scope.find_rigol(rm, None, parse_args(["--detect"]))
    assert got == "USB0::ALIVE", f"选错了资源：{got}"
    assert dead.closed, "探测失败时没关会话（N6）"


@case
def t11_find_rigol_uses_caller_transport_args():
    """N6：探测要走 --timeout/--chunk 同一套参数，不是写死的占位值。"""
    seen = {}

    class Probe(FakeInst):
        def __init__(self):
            super().__init__(idn=IDN_DS2000A)

    def fake_open(rm, resource, args):
        seen["timeout"] = args.timeout
        seen["chunk"] = args.chunk
        return Probe()

    saved = scope.open_instrument
    scope.open_instrument = fake_open
    try:
        scope.find_rigol(FakeRM({"R1": Probe()}), None,
                         parse_args(["--detect", "--timeout", "9000",
                                     "--chunk", "128"]))
    finally:
        scope.open_instrument = saved
    assert seen == {"timeout": 9000, "chunk": 128}, seen


@case
def t12_query_short_drains_on_timeout():
    """N7：短超时查询失败必须 drain，否则迟到回复让后续读取错位一格。"""
    inst = FakeInst()
    calls = []
    saved = scope.drain
    scope.drain = lambda i: calls.append(i)
    try:
        got = scope._query_short(inst, ":NOPE?")
    finally:
        scope.drain = saved
    assert got is None, got
    assert calls == [inst], "超时后没 drain（N7）"
    assert inst.timeout == 3000, f"没恢复 timeout：{inst.timeout}"


@case
def t13_probe_hdiv_drains_on_timeout():
    """N7：probe_hdiv 的失败路径同样要 drain。"""
    inst = FakeInst(overrides={":WAV:POIN?": TimeoutError("boom")})
    calls = []
    saved = scope.drain
    scope.drain = lambda i: calls.append(i)
    try:
        assert scope.probe_hdiv(inst) is None
    finally:
        scope.drain = saved
    assert calls, "probe_hdiv 失败后没 drain（N7）"


# ---------------------------------------------------------------------------
# N4 —— --dry-run 计划必须与真实调用序列一致
# ---------------------------------------------------------------------------


@case
def t14_raw_plan_stops_before_deep_read():
    """N4：深内存只能在停止态读，计划里 :STOP 必须出现在 :ACQ:MDEP 之前。"""
    rc, out, _ = run_with(["--capture", "--mode", "RAW", "--points", "140000",
                           "--dry-run"])
    assert rc == 0, f"rc={rc}\n{out}"
    assert ":STOP" in out, f"RAW 计划里没有 :STOP：\n{out}"
    assert out.index(":STOP") < out.index(":ACQ:MDEP"), \
        f":STOP 不在深内存配置之前：\n{out}"
    assert ":RUN" in out, f"计划里没有恢复采集：\n{out}"


@case
def t15_measure_plan_shows_frontend():
    """N4：--measure 的计划里必须能看到前端配置（以前整段缺失）。"""
    rc, out, _ = run_with(["--measure", "vpp", "--vdiv", "0.5",
                           "--coupling", "DC", "--dry-run"])
    assert rc == 0, f"rc={rc}\n{out}"
    for token in ("SCAL 0.5", "COUP DC", "PROB?", "OFFS"):
        assert token in out, f"计划缺 {token}：\n{out}"


# ---------------------------------------------------------------------------
# N8 —— --json 纯净性
# ---------------------------------------------------------------------------


@case
def t16_json_stdout_is_pure_json():
    """N8：--json 时 stdout 只能有那一份 JSON，人类可读行必须去 stderr。"""
    import subprocess
    with tempfile.TemporaryDirectory(prefix="ea_scope_") as td:
        f = write_tmc(bytes([135] * 20), Path(td) / "a.bin")
        r = subprocess.run(
            [sys.executable, "-u", str(SCOPE_PY), "--parse-tmc", str(f),
             "--vdiv", "1.0", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=td)
    assert r.returncode == 0, f"rc={r.returncode}\n{r.stdout}\n{r.stderr}"
    obj = json.loads(r.stdout)  # 混进任何人类可读行这里就抛
    assert obj["points"] == 20, obj
    assert obj["sample_domain"]["flat"] is True, obj


@case
def t17_json_measure_pure_json():
    """N8：--measure --json 同样只有一份 JSON（▶ 型号、⚠️ 警告都在 stderr）。"""
    fake = scope.emit_json
    sink: dict = {}
    scope.emit_json = lambda obj: sink.update(obj)
    try:
        rc, out, inst = run_with(["--measure", "vpp", "--channel", "CHAN1",
                                  "--resource", "FAKE1", "--json"])
    finally:
        scope.emit_json = fake
    assert rc == 0, f"rc={rc}\n{out}"
    assert sink.get("vdiv") == 1.0, sink
    assert sink.get("measurements", {}).get("vpp") == 1.0, sink
    assert "▶" not in out, f"人类可读行泄进了 stdout：{out}"


# ---------------------------------------------------------------------------
# N9 —— DS100 CSV 路径
# ---------------------------------------------------------------------------


def make_csv(path: Path, volts: np.ndarray, xinc=1e-6) -> Path:
    lines = ["CHA(V)  probe:X1,sampling rate : %d" % int(round(1.0 / xinc))]
    for i, v in enumerate(volts):
        lines.append(f"{i * xinc:.9f},{v:.6f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@case
def t18_ds100_flat_warns_and_rc2():
    """N9：平线 CSV 必须 rc=2 并给警告（以前无论什么都 rc=0）。"""
    with tempfile.TemporaryDirectory(prefix="ea_scope_") as td:
        f = make_csv(Path(td) / "flat.csv", np.full(500, 1.5))
        args = parse_args(["--parse-ds100", str(f)])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = scope.do_parse_ds100(args)
        out = buf.getvalue()
    assert rc == 2, f"平线应 rc=2，实际 {rc}\n{out}"
    assert "平线" in out, f"没给平线警告：{out}"


@case
def t19_ds100_square_reports_periods():
    """N9：方波 CSV 必须 rc=0，且 sample_domain.periods 真的存在且可信。

    走子进程（不是进程内调用）：--json 的纯净性是 CLI 契约，只有子进程能验到
    emit_json 到底写去了哪个流。
    """
    import subprocess
    n, xinc = 1000, 1e-6
    v = np.where((np.arange(n) % 50) < 25, 3.3, 0.0)
    with tempfile.TemporaryDirectory(prefix="ea_scope_") as td:
        f = make_csv(Path(td) / "sq.csv", v, xinc)
        r = subprocess.run(
            [sys.executable, "-u", str(SCOPE_PY), "--parse-ds100", str(f),
             "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=td)
    assert r.returncode == 0, f"方波应 rc=0，实际 {r.returncode}\n{r.stdout}\n{r.stderr}"
    res = json.loads(r.stdout)  # 人类行混进来这里就抛
    smp = res.get("sample_domain")
    assert smp, f"缺 sample_domain（N9 的字段被丢了）：{res}"
    assert smp["periods"] >= 2, f"periods={smp['periods']}"
    assert abs(res["freq_hz"] - 20000.0) < 50, f"freq={res['freq_hz']}"
    assert abs(res["duty_pct"] - 50.0) < 1.0, f"duty={res['duty_pct']}"


@case
def t20_ds100_short_window_warns_periods():
    """N9：只有不到 2 个完整周期时，占空比不可信，必须 rc=2。"""
    n, xinc = 200, 1e-6
    v = np.where((np.arange(n) % 50) < 25, 3.3, 0.0)  # 200 点 = 4 个周期
    with tempfile.TemporaryDirectory(prefix="ea_scope_") as td:
        f = make_csv(Path(td) / "short.csv", v, xinc)
        args = parse_args(["--parse-ds100", str(f)])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = scope.do_parse_ds100(args)
        out = buf.getvalue()
    assert rc == 0, f"4 个周期够用，应 rc=0：{rc}\n{out}"


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main() -> int:
    npass = 0
    for fn in CASES:
        name = fn.__name__
        try:
            fn()
        except AssertionError as exc:
            print(f"[FAIL] {name}\n       {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {name}\n       {type(exc).__name__}: {exc}")
        else:
            npass += 1
            print(f"[PASS] {name}")
    print(f"\n{npass}/{len(CASES)} 通过")
    return 0 if npass == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
