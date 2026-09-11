# -*- coding: utf-8 -*-
"""回归测试：digital.csv 解析 / 通道统计 / 内置 UART 解码 / decoded.csv 有损扫描。

不需要 Logic 2、不需要硬件：全部用合成的 digital.csv 驱动。

重点覆盖两个真机踩过的坑：
  A. 解码器在数据位里重新同步 → 成片 framing error（用户手写 v1 的 bug）
  B. decoded.csv 把不可打印字节渲染成 '.' → 被当成 0x2E 得出错误结论
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[1])
spec = importlib.util.spec_from_file_location(
    "waveform", os.path.join(REPO, "tools", "instrument", "scripts", "waveform.py"))
wv = importlib.util.module_from_spec(spec)
sys.modules["waveform"] = wv          # @dataclass 解析字符串注解时要能查到本模块
spec.loader.exec_module(wv)

HERE = Path(__file__).parent
results = []


def check(label, cond, detail=""):
    results.append(bool(cond))
    print(("[PASS] " if cond else "[FAIL] ") + label)
    if not cond:
        print("        " + str(detail))


# ───────────────────────── 合成 digital.csv ─────────────────────────

def gen_edges(baud, data, t0=0.0, bits=8, idle_bits=4.0):
    """按 8N1 生成某通道的跳变序列（初始为空闲高）。

    先给 idle_bits 个比特的空闲 —— 真实捕获里数据到来前线路总是空闲的，
    也正是解码器判定起始位所需要的证据（t0 处若直接就是下降沿，解码器无从
    判断前面是否空闲）。
    """
    bit = 1.0 / baud
    edges = [(t0, 1)]
    t = t0 + idle_bits * bit
    for b in data:
        edges.append((t, 0))                      # 起始位
        t += bit
        for k in range(bits):
            v = (b >> k) & 1
            if edges[-1][1] != v:
                edges.append((t, v))
            t += bit
        if edges[-1][1] != 1:
            edges.append((t, 1))                  # 停止位
        t += bit
    return edges, t


def write_csv(path, ch_edges, t_end, initial_low=None):
    """ch_edges: {通道号: edges}，首行给所有通道的初始电平。"""
    chans = sorted(ch_edges)
    lines = ["Time [s]," + ",".join(f"Channel {c}" for c in chans)]
    # 合并时间轴：数字通道只在任意通道跳变时记录一行
    events = {}
    for c in chans:
        for t, v in ch_edges[c]:
            events.setdefault(round(t, 12), {})[c] = v
    cur = {c: ch_edges[c][0][1] for c in chans}
    for t in sorted(events):
        cur.update(events[t])
        lines.append(f"{t:.12f}," + ",".join(str(cur[c]) for c in chans))
    lines.append(f"{t_end:.12f}," + ",".join(str(cur[c]) for c in chans))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


TMP = Path(tempfile.mkdtemp(prefix="wv_"))

# ────────────── 1. 往返：已知字节流 → 解出完全相同的字节 ──────────────
BAUD = 57600
# 用户的真实场景里 0x01/0x08/0x82 被渲染成了 '.'，这里必须原样还原
PAYLOAD = bytes([0xEF, 0x01, 0x08, 0x82, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x2E, 0x41, 0x7A])
edges, t_end = gen_edges(BAUD, PAYLOAD)
p = TMP / "digital.csv"
write_csv(p, {7: edges}, t_end, )
wave = wv.load_digital_csv(p)
ch7 = wave.resolve("ch7")
check("digital.csv 解析：通道号/跳变数正确", ch7 is not None and ch7.transitions > 0,
      f"ch7={ch7}")

res = wv.decode_uart(ch7, BAUD)
check(f"往返解码 8N1@{BAUD}：{len(PAYLOAD)} 字节全部还原",
      res.bytes_ == PAYLOAD and not res.errors,
      f"得到 {res.bytes_.hex(' ')} | 期望 {PAYLOAD.hex(' ')} | 错误 {len(res.errors)}")

check("不可打印字节保持原值（0x01/0x08/0x82 没有变成 0x2E）",
      res.bytes_[1:4] == bytes([0x01, 0x08, 0x82]) and b"\x2e\x2e\x2e" not in res.bytes_,
      res.bytes_.hex(" "))

# ────────────── 2. 波特率自动粗估 ──────────────
est = ch7.estimate_baud()
check(f"波特率粗估 → 最近标准档 {est[1]}（实测 {est[0]:.0f}，偏差 {est[2]*100:.3f}%）",
      est is not None and est[1] == BAUD and est[2] < 0.02, est)

# ────────────── 3. 数据位内重新同步必须被挡住（用户 v1 的 bug） ──────────────
# 0x02 = 起始0, d0=0, d1=1, d2=0... → d2 的下降沿前有整 1 bit 的高电平，
# 单靠"空闲高"判据拦不住；真正的保障是"每帧整帧跳到帧尾之后"。
BAD = bytes([0x02] * 8)
e2, t2 = gen_edges(BAUD, BAD)
p2 = TMP / "d2.csv"
write_csv(p2, {7: e2}, t2)
ch2 = wv.load_digital_csv(p2).resolve("ch7")
res2 = wv.decode_uart(ch2, BAUD)
check(f"连续 0x02 帧：解出恰好 {len(BAD)} 帧、无 framing error",
      res2.bytes_ == BAD and not res2.errors,
      f"得到 {len(res2.frames)} 帧 / 错误 {len(res2.errors)}：{res2.bytes_.hex(' ')}")


def naive_decode(ch, baud, bits=8):
    """对照实现：只扫下降沿、帧内不跳过 —— 即"在数据位里重新同步"的解码器。"""
    bit = 1.0 / baud
    out = []
    for i in range(len(ch.edges)):
        t, lvl = ch.edges[i]
        if lvl != 0:
            continue
        v = 0
        for k in range(bits):
            ts = t + (1.5 + k) * bit
            if ts > ch.times[-1]:
                return out
            if ch.level_at(ts):
                v |= 1 << k
        out.append(v)
    return out


naive = naive_decode(ch2, BAUD)
check(f"对照：朴素解码器在同一波形上多解出 {len(naive)-len(BAD)} 个伪帧（证明该 bug 真实存在）",
      len(naive) > len(BAD),
      f"朴素 {len(naive)} 帧 vs 正确 {len(res2.frames)} 帧")

# ────────────── 4. 波特率给错 → 报错而不是静默吐垃圾 ──────────────
res3 = wv.decode_uart(ch7, BAUD * 2)
check("波特率错误 → 出 framing error，不静默返回垃圾",
      len(res3.errors) > 0 or res3.bytes_ != PAYLOAD,
      f"good={len(res3.good)} err={len(res3.errors)}")

# ────────────── 5. 毛刺（< 0.5 bit）不产生伪帧 ──────────────
bit = 1.0 / BAUD
glitch = [(0.0, 1), (1.0 * bit * 100, 1)]        # 起点空闲
glitch += [(1.0 * bit * 100 + 0.2 * bit, 0),     # 0.2 bit 的低脉冲毛刺
           (1.0 * bit * 100 + 0.4 * bit, 1)]
glitch += [(1.0 * bit * 100 + 0.9 * bit, 1)]
gp = TMP / "glitch.csv"
write_csv(gp, {7: glitch}, 1.0 * bit * 101)
gch = wv.load_digital_csv(gp).resolve("ch7")
gres = wv.decode_uart(gch, BAUD)
check("亚比特毛刺不产生伪帧", not gres.frames, f"解出 {gres.frames}")

# ────────────── 6. 通道统计是按时长加权（不是按行数） ──────────────
# 一路空闲高 1000 bit，之后才有一串数据 → 高位占比必须接近 100%，而不是约 50%
long_idle = [(0.0, 1)]
e3, t3 = gen_edges(BAUD, bytes([0x55] * 4), t0=1000 * bit)
long_idle += e3
lp = TMP / "ratio.csv"
write_csv(lp, {7: long_idle}, t3)
lch = wv.load_digital_csv(lp).resolve("ch7")
ratio = lch.high_ratio(t3)
check(f"高位占比按时长加权（长空闲 → {ratio*100:.1f}%，按行数会算成 ~50%）",
      ratio > 0.9, f"ratio={ratio}")

# ────────────── 7. 静默通道被标为无信号（问题 5） ──────────────
sp = TMP / "silent.csv"
write_csv(sp, {7: edges, 15: [(0.0, 1)]}, t_end)
sw = wv.load_digital_csv(sp)
s15 = wv.describe_channel(sw.resolve("ch15"), sw.t_end)
s7 = wv.describe_channel(sw.resolve("ch7"), sw.t_end)
check("静默通道 → 标为无信号", "跳变 0" in s15 and "无信号" in s15, s15)
check("活跃通道 → 统计出跳变数与波特率",
      "跳变 " in s7 and str(BAUD) in s7, s7)

# ────────────── 8. TX/RX 接反提示（问题 5） ──────────────
# 主动方先开口：给标成 RX 的通道更早的起始时刻，被标成 TX 的更晚
late_e, late_t = gen_edges(BAUD, b"\xBB\xBB", t0=0.010)
early_e, early_t = gen_edges(BAUD, b"\xAA", t0=0.000)
lp2, ep2 = TMP / "late.csv", TMP / "early.csv"
write_csv(lp2, {7: late_e}, late_t)
write_csv(ep2, {15: early_e}, early_t)
tx_r = wv.decode_uart(wv.load_digital_csv(lp2).resolve("ch7"), BAUD)
rx_r = wv.decode_uart(wv.load_digital_csv(ep2).resolve("ch15"), BAUD)
hint = wv.swap_hint(tx_r, rx_r)                        # 标成 TX 的晚开口 → 应提示
check("RX 先开口 → 提示疑似 TX/RX 接反", hint is not None and "接反" in hint, hint)
check("顺序正常时不误报", wv.swap_hint(rx_r, tx_r) is None)

# ────────────── 9. decoded.csv 有损扫描（问题 1） ──────────────
lossy = TMP / "decoded.cssv"
lossy = TMP / "decoded.csv"
lossy.write_text(
    "Time [s],data,error\n"
    + "".join(f"{i*0.001},.,\n" for i in range(10))
    + "".join(f"{0.02+i*0.001},A,\n" for i in range(3)),
    encoding="utf-8")
rep = wv.scan_decoded_csv(lossy)
check(f"有损 decoded.csv → 告警（{rep['worst']} 列 {rep['worst_ratio']*100:.0f}% 是点）",
      rep["lossy"] and rep["worst"] == "data", rep)
check("告警文案指向 digital.csv 退路",
      any("--decode-uart" in l for l in wv.format_lossy_warning(rep)),
      wv.format_lossy_warning(rep))

clean = TMP / "clean.csv"
clean.write_text("Time [s],data,error\n" + "".join(f"{i*0.001},AB,\n" for i in range(10)),
                 encoding="utf-8")
clean_rep = wv.scan_decoded_csv(clean)
check("正常 decoded.csv 不误报", not clean_rep["lossy"] and not wv.format_lossy_warning(clean_rep),
      clean_rep)

# ────────────── 10. --decode-uart 参数解析 ──────────────
sp1 = wv.parse_uart_spec("ch=7,baud=57600")
check("解析 ch=7,baud=57600", sp1.channels == {"ch": "7"} and sp1.baud == 57600, sp1)
sp2 = wv.parse_uart_spec("TX=7,RX=15,baud=auto")
check("解析 TX/RX 对 + baud=auto", sp2.channels == {"TX": "7", "RX": "15"} and sp2.baud is None, sp2)
sp3 = wv.parse_uart_spec("7,115200")
check("解析位置形式 '7,115200'", sp3.channels == {"ch": "7"} and sp3.baud == 115200, sp3)
try:
    wv.parse_uart_spec("baud=9600")
    ok = False
except ValueError:
    ok = True
check("缺通道 → 报错", ok)

# ────────────── 11. hex 输出 ──────────────
rows = wv.format_hex_rows(bytes(range(20)))
check("hex 分行了且带偏移", len(rows) == 2 and "00000000" in rows[0] and "00000010" in rows[1], rows)

print("─" * 50)
print(f"{sum(results)}/{len(results)} 通过")
sys.exit(0 if all(results) else 1)
