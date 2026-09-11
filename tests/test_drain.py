# -*- coding: utf-8 -*-
"""回归测试：`--rtt snapshot` 只读不推进 RdOff 导致的缓冲写满缺陷。

缺陷现象（真机复现）：采集到第 8 轮时 WrOff 冻结在 2046（2048B 缓冲写满），
Flags=0（NO_BLOCK_SKIP），此后目标的 SEGGER_RTT_printf 被静默丢弃 —— 表象像
"目标卡死"，实际目标在正常跑。若 Flags=2（BLOCK_IF_FIFO_FULL），目标会被永久
锁死在等 RdOff 的自旋里。

修复：采集后把 RdOff 推进到已读位置（轮询时搭下一轮脚本的车，收尾再补一次）。
"""
import builtins
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[1])
HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(HERE, "fake_jlink.bat")
STATE = os.path.join(HERE, "_drain_state.json")
CB = "0x200014DC"
RD_OFF = 0x200014DC + 40          # aUp[0].RdOff

spec = importlib.util.spec_from_file_location(
    "jd", os.path.join(REPO, "tools", "jlink-debug", "scripts", "jlink_debug.py"))
jd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jd)
jd.DEFAULT_DEVICE = "N32G4FRRE"

REAL_SCAN = jd.scan_occupying_processes


def mkargs(**kw):
    base = dict(cb_addr=None, dry_run=False, reset_run=False, encoding="auto",
                duration=None, interval=None, poll=1, json=True, out=None,
                force=False, consume=True, jlink=FAKE, device="N32G4FRRE",
                interface="SWD", speed=4000)
    base.update(kw)
    return types.SimpleNamespace(**base)


def set_env(size="0x100", flags="0", follow="0", ignore_w4="0"):
    for k in ("FAKE_UP_SIZE", "FAKE_RTT_FLAGS", "FAKE_RD_FOLLOW", "FAKE_IGNORE_W4"):
        os.environ.pop(k, None)
    os.environ.update({"FAKE_UP_SIZE": size, "FAKE_RTT_FLAGS": flags,
                       "FAKE_RD_FOLLOW": follow, "FAKE_IGNORE_W4": ignore_w4,
                       "FAKE_STATE": STATE})
    if os.path.exists(STATE):
        os.unlink(STATE)


def seed_state(size, wr, rd, flags_buf=None):
    """直接构造目标侧 RAM 初值（用于"采集时缓冲已满"这类场景）。"""
    with open(STATE, "w") as f:
        json.dump({"buf": bytes(size).hex(), "wr": wr, "rd": rd, "follow": False}, f)


def state():
    with open(STATE) as f:
        return json.load(f)


def run(args):
    jd.scan_occupying_processes = lambda images=jd.JLINK_OCCUPYING_IMAGES: {}
    buf = []
    real_print = builtins.print
    builtins.print = lambda *a, **k: buf.append(" ".join(str(x) for x in a))
    try:
        rc = jd.do_rtt_snapshot(args, {})
    finally:
        builtins.print = real_print
        jd.scan_occupying_processes = REAL_SCAN
    return rc, "\n".join(buf)


results = []


def check(label, cond, detail=""):
    results.append(cond)
    print(("[PASS] " if cond else "[FAIL] ") + label)
    if not cond:
        print("        " + detail)


# 64B 缓冲（可用 63B）/ 每轮约 12B → 第 5~6 轮就写满，12 轮足以复现冻结。
# 用大缓冲+多轮次要跑几分钟（每次 JLink 启动约 5s），没必要。
POLL = 12
ROUNDS = POLL + 1                 # 首轮只读控制块、不占采样次数，故实际轮次多一次

# ---- 1. 缺陷复现：--no-consume 下缓冲写满 → WrOff 冻结、日志丢失 ----
set_env(size="0x40")
rc, out_a = run(mkargs(cb_addr=CB, poll=POLL, consume=False, json=False))
st_a = state()
ticks_a = out_a.count("tick wr=")
check("复现：--no-consume 时缓冲写满，"f"只收到 {ticks_a}/{ROUNDS} 轮日志",
      ticks_a < ROUNDS and st_a["rd"] == 0 and "无新增" in out_a,
      f"wr={st_a.get('wr')} rd={st_a.get('rd')} ticks={ticks_a}\n{out_a[-400:]}")

# ---- 2. 修复：默认消费 → 缓冲不写满，一轮不丢 ----
set_env(size="0x40")
rc, out_b = run(mkargs(cb_addr=CB, poll=POLL, json=False))
st_b = state()
ticks_b = out_b.count("tick wr=")
check(f"修复：默认推进 RdOff → 全程 {ROUNDS}/{ROUNDS} 轮日志一个不丢",
      ticks_b == ROUNDS, f"只收到 {ticks_b}/{ROUNDS}\n{out_b[-400:]}")
check("修复：收尾把 RdOff 推进到已读位置",
      "RdOff 已推进到" in out_b and st_b["rd"] > 0,
      f"wr={st_b.get('wr')} rd={st_b.get('rd')}\n{out_b[-300:]}")
# fake 每次脚本执行前都会推 wr —— 收尾那次也一样，所以判据是"缓冲基本清空"
# （rd 追到只差最后一行），而不是 wr == rd。
check("修复：收尾后缓冲基本清空（rd 追上 wr 只差一行）",
      0 <= st_b["wr"] - st_b["rd"] <= 16,
      f"wr={st_b.get('wr')} rd={st_b.get('rd')} 残留={st_b['wr'] - st_b['rd']}")
# 搭车写回生效的证据：只靠收尾那一次的话，中途必然已经写满 —— 而 ticks_b 是满的
check("修复：中途就在释放空间（非只靠收尾那一次）",
      ticks_b == ROUNDS and ticks_b > ticks_a,
      f"consume={ticks_b} vs no-consume={ticks_a}")

# ---- 3. 写回不生效必须报出来（不能静默）----
set_env(size="0x100", ignore_w4="1")
rc, out = run(mkargs(cb_addr=CB, poll=1, json=False))
check("写回被拒 → 明确报告失败，不静默",
      "收尾写回失败" in out, out[-400:])

# ---- 4. 轮询中途发现写回没生效（下一轮回读校验）----
set_env(size="0x100", ignore_w4="1")
rc, out = run(mkargs(cb_addr=CB, poll=5, json=False))
check("写回被拒 → 中途轮询即报「写回未生效」",
      "写回未生效" in out, out[-600:])

# ---- 5. Flags=BLOCKING + 缓冲接近写满 → 点明会锁死目标 ----
set_env(size="0x100", flags="2")
seed_state(0x100, 250, 0)
rc, out = run(mkargs(cb_addr=CB, poll=1, json=False))
check("BLOCKING 模式：提示目标可能被锁死在自旋",
      "BLOCK_IF_FIFO_FULL" in out and "锁死" in out, out[-600:])

# ---- 6. Flags=NO_BLOCK_SKIP → 点明日志已被静默丢弃 ----
set_env(size="0x100", flags="0")
seed_state(0x100, 250, 0)
rc, out = run(mkargs(cb_addr=CB, poll=1, json=False))
check("SKIP 模式：提示日志已丢但目标在正常跑",
      "NO_BLOCK_SKIP" in out and "日志已经丢了" in out, out[-600:])

# ---- 7. 缓冲未满时不刷模式噪音 ----
set_env(size="0x800")
rc, out = run(mkargs(cb_addr=CB, poll=1, json=False))
check("缓冲未满 → 不刷写满警告", "接近写满" not in out, out[-400:])

# ---- 8. --json 暴露 consume / 模式 / 写回结果 ----
set_env(size="0x800")
rc, out = run(mkargs(cb_addr=CB, poll=1, json=True))
line = [l for l in out.splitlines() if l.startswith("{")]
ok = bool(line)
if ok:
    j = json.loads(line[-1])
    ok = (j.get("consume") is True and j.get("drain_failed") is False
          and j.get("rtt_mode") == "NO_BLOCK_SKIP" and "wr" in j)
check("--json 含 consume/rtt_mode/drain_failed/wr", ok, line[-1] if line else "无 JSON")

# ---- 9. dry-run 要说明会写 RdOff ----
set_env(size="0x800")
rc, out = run(mkargs(cb_addr=CB, dry_run=True, json=False))
check("dry-run 列出 w4 写回步骤",
      f"w4 0x{RD_OFF:08X}" in out, out)

# ---- 10. 回归：正常单次快照仍取到数据 ----
set_env(size="0x800")
rc, out = run(mkargs(cb_addr=CB, poll=1, json=False))
check("回归：正常采集仍出数据", "历史" in out and rc == 0, out[-400:])

os.environ.pop("FAKE_STATE", None)
if os.path.exists(STATE):
    os.unlink(STATE)
print("─" * 50)
print(f"{sum(results)}/{len(results)} 通过")
sys.exit(0 if all(results) else 1)
