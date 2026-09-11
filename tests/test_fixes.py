# -*- coding: utf-8 -*-
"""回归测试：S1（截胡静默丢日志）/ S2（错误原因合并）/ S3（--force 适用范围）。

无硬件，用 fake_jlink.bat 模拟 JLink Commander 的 savebin 通道。
"""
import importlib.util
import os
import sys
import types
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[1])
HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(HERE, "fake_jlink.bat")
MAP = os.path.join(HERE, "test.map")
# 本套件专用的 fake 目标 RAM。用例间必须清空：rd/wr 会累积，不清就让用例不再
# 测它自己声明的场景（S1-c/S1-d 依赖"首轮空缓冲"，被上一个用例推进过 rd 就失效）
STATE = os.path.join(HERE, "_fixes_state.json")
os.environ["FAKE_STATE"] = STATE

spec = importlib.util.spec_from_file_location(
    "jd", os.path.join(REPO, "tools", "jlink-debug", "scripts", "jlink_debug.py"))
jd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jd)
jd.DEFAULT_DEVICE = "N32G4FRRE"

def no_scan(images=jd.JLINK_OCCUPYING_IMAGES):
    """「本机没有 RTT 读取端进程」。

    除 S1-a/S1-b/S1-c 显式传入假扫描外，各用例一律以这个前提起跑。**不能退回真实
    扫描**：那样机器上只要真开着 JLinkRTTViewer，占用检查就会把用例提前拦下，
    它们便测不到自己声明的分支（曾因此在开着 RTTViewer 的机器上 4 个用例集体假红）。
    「有没有检测到读取端」正是 S1-d 要固定的那个变量。
    """
    return {}


def mkargs(**kw):
    base = dict(cb_addr=None, dry_run=False, reset_run=False, encoding="auto",
                duration=None, interval=None, poll=1, json=True, out=None,
                force=False, jlink=FAKE, device="N32G4FRRE",
                interface="SWD", speed=4000)
    base.update(kw)
    return types.SimpleNamespace(**base)


def run(label, args, scan=None, expect_rc=None, expect_out=(), expect_absent=()):
    if os.path.exists(STATE):          # 每个用例都从干净的目标 RAM 起跑
        os.unlink(STATE)
    jd.scan_occupying_processes = scan or no_scan
    buf = []
    real_print = print
    import builtins
    builtins.print = lambda *a, **k: buf.append(" ".join(str(x) for x in a))
    try:
        rc = jd.do_rtt_snapshot(args, {})
    finally:
        builtins.print = real_print
    out = "\n".join(buf)
    ok = True
    msgs = []
    if expect_rc is not None and rc != expect_rc:
        ok = False
        msgs.append(f"rc={rc} 期望 {expect_rc}")
    for s in expect_out:
        if s not in out:
            ok = False
            msgs.append(f"缺少输出 {s!r}")
    for s in expect_absent:
        if s in out:
            ok = False
            msgs.append(f"不该出现 {s!r}")
    print(("[PASS] " if ok else "[FAIL] ") + label)
    if not ok:
        for m in msgs:
            print("        " + m)
        print("        --- 实际输出 ---")
        for line in out.splitlines():
            print("        " + line)
    return ok


results = []

# ---- S1-a：命中占用进程且未加 --force → 中止 ----
def fake_scan_rttviewer(images=jd.JLINK_OCCUPYING_IMAGES):
    return {"JLinkRTTViewer.exe": [37320]}


results.append(run(
    "S1-a 占用检测阻断（无 --force）",
    mkargs(cb_addr="0x200014DC"),
    scan=fake_scan_rttviewer,
    expect_rc=1,
    expect_out=["37320", "已中止采集", "--force", "持续推进 RdOff"],
))

# ---- S1-b：加 --force → 放行 ----
results.append(run(
    "S1-b --force 放行",
    mkargs(cb_addr="0x200014DC", force=True),
    scan=fake_scan_rttviewer,
    expect_rc=0,
    expect_out=["--force：继续采集"],
    expect_absent=["已中止采集"],
))

# ---- S1-c：缓冲被取空（rd==wr）且有读取端 → 指明被截胡 ----
os.environ["FAKE_RD_FOLLOW"] = "1"
results.append(run(
    "S1-c 空缓冲 + 有读取端 → 指明被截胡",
    mkargs(cb_addr="0x200014DC", force=True),
    scan=fake_scan_rttviewer,
    expect_rc=0,
    expect_out=["环形缓冲为空", "已被其它 RTT 读取端取空", "未取到任何数据（0 字节）"],
))

# ---- S1-d：缓冲为空但无读取端 → 归因目标未输出 ----
# 仍然让 rd 追住 wr（两种成因下 rd==wr 的表象相同），唯一的差别是**有没有检测到
# 读取端进程** —— 那才是这段分支真正要区分的东西，也是本用例要固定的变量。
results.append(run(
    "S1-d 空缓冲 + 无读取端 → 归因目标未输出",
    mkargs(cb_addr="0x200014DC"),
    expect_rc=0,
    expect_out=["环形缓冲为空", "目标尚未通过 RTT 输出"],
    expect_absent=["已被其它 RTT 读取端取空"],
))
os.environ.pop("FAKE_RD_FOLLOW", None)

# ---- S2-a：地址错 → cb-id，引导查符号 ----
results.append(run(
    "S2-a 地址错 → 提示查符号（不提探针）",
    mkargs(cb_addr="0x20008000"),
    expect_rc=0,
    expect_out=["地址无效", "不是 SEGGER_RTT_CB", "--cb-addr"],
    expect_absent=["探针被占用"],
))

# ---- S2-b：连接失败 → no-read，引导查进程 ----
results.append(run(
    "S2-b 连接失败 → 提示查占用进程（不提符号）",
    mkargs(cb_addr="0x200014DC", jlink=os.path.join(HERE, "fake_fail.bat")),
    expect_rc=0,
    expect_out=["连接失败", "RTTViewer/Logger/GDBServer"],
    expect_absent=["核对 .map"],
))

# ---- 正常路径没被改坏 ----
results.append(run(
    "回归 正常采集仍出数据",
    mkargs(cb_addr="0x200014DC"),
    expect_rc=0,
    expect_out=["历史"],
    expect_absent=["未取到任何数据"],
))

print("─" * 50)
print(f"{sum(results)}/{len(results)} 通过")
sys.exit(0 if all(results) else 1)
