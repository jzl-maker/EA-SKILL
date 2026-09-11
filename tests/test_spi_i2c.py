# -*- coding: utf-8 -*-
"""回归测试：内置 SPI / I2C 解码（全部用合成 digital.csv，无需硬件）。

覆盖：四种 SPI mode、CS 切帧 / 无 CS 按时钟间隙切帧、MSB/LSB、CPOL 自检、
I2C 的 START/STOP/repeated START/地址读写位/ACK/NACK，以及几种典型接线错误。
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
sys.modules["waveform"] = wv
spec.loader.exec_module(wv)

TMP = Path(tempfile.mkdtemp(prefix="spii2c_"))
results = []


def check(label, cond, detail=""):
    results.append(bool(cond))
    print(("[PASS] " if cond else "[FAIL] ") + label)
    if not cond:
        print("        " + str(detail))


class Builder:
    """按时间戳累积各通道的跳变，最后写成 digital.csv 并加载。"""

    def __init__(self):
        self.ch: dict[int, list[tuple[float, int]]] = {}
        self.t = 0.0

    def set(self, channel: int, level: int, t: float | None = None):
        t = self.t if t is None else t
        lst = self.ch.setdefault(channel, [])
        if not lst:
            lst.append((0.0, level if t > 0 else level))
        if lst[-1][1] != level:
            lst.append((t, level))
        return self

    def init(self, channel: int, level: int):
        self.ch.setdefault(channel, [(0.0, level)])
        return self

    def wave(self, name="w.csv"):
        chans = sorted(self.ch)
        # 合并时间轴
        ev: dict[float, dict[int, int]] = {}
        for c in chans:
            for t, v in self.ch[c]:
                ev.setdefault(round(t, 12), {})[c] = v
        cur = {c: self.ch[c][0][1] for c in chans}
        lines = ["Time [s]," + ",".join(f"Channel {c}" for c in chans)]
        for t in sorted(ev):
            cur.update(ev[t])
            lines.append(f"{t:.12f}," + ",".join(str(cur[c]) for c in chans))
        lines.append(f"{self.t:.12f}," + ",".join(str(cur[c]) for c in chans))
        p = TMP / name
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return wv.load_digital_csv(p)


# ═══════════════════════ SPI ═══════════════════════

CLK, MOSI, MISO, CS = 0, 1, 2, 3
PERIOD = 1.0 / 1_000_000          # 1 MHz


def spi_build(mosi_data, miso_data, *, cpol=0, cpha=0, bits=8, cs=True,
              order="msb", gap=0.0, name="spi.csv"):
    """按给定 mode 生成 SPI 波形。CPHA=0 在前沿采样、数据在前沿之前就绪。"""
    b = Builder()
    idle = cpol
    b.init(CLK, idle)
    b.init(MOSI, 0 if order == "msb" else 0)
    b.init(MISO, 0)
    if cs:
        b.init(CS, 1)
    n = max(len(mosi_data or b""), len(miso_data or b""))
    t = 10 * PERIOD
    for i in range(n):
        mv = mosi_data[i] if mosi_data and i < len(mosi_data) else None
        sv = miso_data[i] if miso_data and i < len(miso_data) else None
        if cs:
            b.set(CS, 0, t)
            t += PERIOD
        # 数据在"采样边沿之前"就绪
        bits_m = ([(mv >> (7 - k)) & 1 for k in range(bits)] if mv is not None
                  else [0] * bits)
        bits_s = ([(sv >> (7 - k)) & 1 for k in range(bits)] if sv is not None
                  else [0] * bits)
        if order == "lsb":
            bits_m, bits_s = bits_m[::-1], bits_s[::-1]
        lead = 1 - idle
        for k in range(bits):
            # CPHA=0：数据在前沿**之前**就绪；CPHA=1：数据在**前沿**翻转（真实行为）
            td = t if cpha == 0 else t + PERIOD * 0.25
            b.set(MOSI, bits_m[k], td)
            b.set(MISO, bits_s[k], td)
            b.set(CLK, lead, t + PERIOD * 0.25)      # 前沿
            b.set(CLK, idle, t + PERIOD * 0.75)      # 后沿
            t += PERIOD
        if cs:
            b.set(CS, 1, t)
            t += 2 * PERIOD
        t += gap
    b.t = t
    return b.wave(name)


def spi_decode(wave, **kw):
    return wv.decode_spi(wave.by_index(CLK), wave.by_index(MOSI),
                         wave.by_index(MISO), wave.by_index(kw.pop("cs", CS)), **kw)


# ---- 1. mode 0 + CS，MOSI/MISO 同时解 ----
MOSI_TX = bytes([0x9F, 0x00, 0x12, 0xAB])
MISO_TX = bytes([0x00, 0xEF, 0x34, 0xCD])
w = spi_build(MOSI_TX, MISO_TX, cpol=0, cpha=0, cs=True)
r = spi_decode(w, mode=0)
check("SPI mode0 + CS：MOSI 全对", bytes(r.stream("mosi")) == MOSI_TX,
      bytes(r.stream("mosi")).hex(" "))
check("SPI mode0 + CS：MISO 全对", bytes(r.stream("miso")) == MISO_TX,
      bytes(r.stream("miso")).hex(" "))
check("SPI mode0：4 个帧段 / 4 个字", r.frames == 4 and len(r.words) == 4, f"{r.frames}/{len(r.words)}")
check("SPI 时钟频率估算 ≈1 MHz",
      r.clk_period and abs(1 / r.clk_period - 1e6) / 1e6 < 0.01, r.clk_period)

# ---- 2. 四种 mode 都要能解对 ----
for mode in (0, 1, 2, 3):
    w = spi_build(MOSI_TX, MISO_TX, cpol=mode >> 1, cpha=mode & 1, cs=True,
                  name=f"spi_m{mode}.csv")
    rr = spi_decode(w, mode=mode)
    check(f"SPI mode{mode} (CPOL={mode>>1} CPHA={mode&1})：MOSI/MISO 全对",
          bytes(rr.stream("mosi")) == MOSI_TX and bytes(rr.stream("miso")) == MISO_TX,
          f"mosi={bytes(rr.stream('mosi')).hex(' ')} miso={bytes(rr.stream('miso')).hex(' ')}")

# ---- 3. mode=auto 自检 CPOL（用 CS 拉高时的时钟电平）----
for cpol in (0, 1):
    w = spi_build(MOSI_TX, None, cpol=cpol, cpha=0, cs=True, name=f"auto{cpol}.csv")
    rr = spi_decode(w, mode=None, cs=CS)
    check(f"mode=auto 自检出 CPOL={cpol} 并解对",
          rr.mode >> 1 == cpol and bytes(rr.stream("mosi")) == MOSI_TX,
          f"mode={rr.mode} {bytes(rr.stream('mosi')).hex(' ')} 依据={rr.detection}")

# ---- 3b. CPHA 是猜的 → 报告必须提示（用 guessed_cpha 而非文案匹配）----
w = spi_build(MOSI_TX, None, cpol=0, cpha=0, cs=True, name="cpha_guess.csv")
auto_r = spi_decode(w, mode=None)
explicit_r = spi_decode(w, mode=0)
auto_txt = "\n".join(wv.format_spi_report(auto_r, {"CLK": "ch0"}))
expl_txt = "\n".join(wv.format_spi_report(explicit_r, {"CLK": "ch0"}))
check("mode=auto → guessed_cpha=True 且报告提示 CPHA 按 0",
      auto_r.guessed_cpha and "CPHA 默认按 0" in auto_txt, auto_txt)
check("显式给 mode → guessed_cpha=False 且不刷该提示",
      not explicit_r.guessed_cpha and "CPHA 默认按 0" not in expl_txt, expl_txt)

# ---- 4. 无 CS：按时钟间隙切帧 ----
w = spi_build(MOSI_TX, None, cpol=0, cpha=0, cs=False, gap=5 * PERIOD, name="nocs.csv")
rr = wv.decode_spi(w.by_index(CLK), w.by_index(MOSI), None, None, mode=0)
check("无 CS：按时钟间隙切出 4 帧、MOSI 全对",
      len(rr.words) == 4 and bytes(rr.stream("mosi")) == MOSI_TX,
      f"{len(rr.words)} 字 {bytes(rr.stream('mosi')).hex(' ')}")

# ---- 5. LSB first ----
w = spi_build(MOSI_TX, None, cpol=0, cpha=0, order="lsb", name="lsb.csv")
rr = spi_decode(w, mode=0, order="lsb")
check("LSB first 解对", bytes(rr.stream("mosi")) == MOSI_TX,
      bytes(rr.stream("mosi")).hex(" "))

# ---- 6. mode 给错 → 结果不对（在 CPHA=1 波形上才是可判别的）----
# 关键：CPHA=1 时数据在**前导沿**翻转。此时若按 CPHA=0 去采（采前导沿），
# 采到的是"上一位"，整串错位。而 CPHA=0 的波形上两种 mode 读到的值相同 ——
# 所以模式误判并不总能被发现，报告才会打印判定依据。
w = spi_build(MOSI_TX, None, cpol=0, cpha=1, cs=True, name="wrongmode.csv")
right = bytes(spi_decode(w, mode=1).stream("mosi"))
wrong = bytes(spi_decode(w, mode=0).stream("mosi"))
check("CPHA=1 波形上 mode 给错 → 解出的字节明显不同（可判别）",
      right == MOSI_TX and wrong != MOSI_TX,
      f"right={right.hex(' ')} wrong={wrong.hex(' ')}")
w0 = spi_build(MOSI_TX, None, cpol=0, cpha=0, cs=True, name="same_0_1.csv")
check("CPHA=0 波形上 mode0/mode1 结果相同（误判不可判别 → 故报告要打印判定依据）",
      bytes(spi_decode(w0, mode=0).stream("mosi"))
      == bytes(spi_decode(w0, mode=1).stream("mosi")) == MOSI_TX)

# ---- 7. 只有 MOSI（单向）时 MISO 位置为 None，不报错 ----
w = spi_build(MOSI_TX, None, cpol=0, cpha=0, cs=True, name="miso_absent.csv")
rr = wv.decode_spi(w.by_index(CLK), w.by_index(MOSI), None, w.by_index(CS), mode=0)
check("未给 miso → MISO 为 None 且 MOSI 正常",
      rr.stream("miso") == [] and bytes(rr.stream("mosi")) == MOSI_TX)

# ---- 8. 时钟空闲电平恰好等于采样电平：不能把起点电平误当边沿 ----
# CPOL=1（空闲高）+ CPHA=1 → 采样沿是上升沿，而 edges[0] 的初始电平就是高
w = spi_build(MOSI_TX, None, cpol=1, cpha=1, cs=True, name="cpol1cpha1.csv")
rr = spi_decode(w, mode=3)
check("CPOL=1 空闲电平=采样电平时不凭空多一个边沿（不加 ragged）",
      bytes(rr.stream("mosi")) == MOSI_TX and rr.ragged == 0,
      f"{bytes(rr.stream('mosi')).hex(' ')} ragged={rr.ragged}")

# ---- 9. 多字（16 bit）----
# 手工造两个 16bit 字：0x1234, 0xABCD
b = Builder()
b.init(CLK, 0); b.init(MOSI, 0); b.init(CS, 1)
t = 10 * PERIOD
for word in (0x1234, 0xABCD):
    b.set(CS, 0, t); t += PERIOD
    for k in range(16):
        b.set(MOSI, (word >> (15 - k)) & 1, t)
        b.set(CLK, 1, t + PERIOD * 0.25)
        b.set(CLK, 0, t + PERIOD * 0.75)
        t += PERIOD
    b.set(CS, 1, t); t += 2 * PERIOD
b.t = t
w16 = b.wave("w16b.csv")
rr = wv.decode_spi(w16.by_index(CLK), w16.by_index(MOSI), None, w16.by_index(CS),
                   mode=0, bits=16)
check("bits=16 时按 16 位拆字",
      [f"{x:04X}" for x in rr.stream("mosi")] == ["1234", "ABCD"],
      " ".join(f"{x:04X}" for x in rr.stream("mosi")))
check("bits>8 时 stream() 仍返回 list[int]（不存在 byte 装不下的问题）",
      all(isinstance(x, int) for x in rr.stream("mosi")))

# ---- 10. 参数解析 ----
s = wv.parse_spi_spec("clk=0,mosi=1,miso=2,cs=3,mode=2")
check("解析 SPI spec", s.signals == {"CLK": "0", "MOSI": "1", "MISO": "2", "CS": "3"}
      and s.mode == 2, s)
check("SPI 别名 sck/sdi/sdo/nss 可识别",
      wv.parse_spi_spec("sck=0,sdi=1").signals == {"CLK": "0", "MOSI": "1"})
check("SPI mode=auto → None", wv.parse_spi_spec("clk=0,mosi=1,mode=auto").mode is None)
for bad, why in (("clk=0", "缺 mosi/miso"), ("mosi=1,clk=0,mode=9", "mode 越界")):
    try:
        wv.parse_spi_spec(bad); ok = False
    except ValueError:
        ok = True
    check(f"SPI 非法参数报错（{why}）", ok)


# ═══════════════════════ I2C ═══════════════════════

SCL, SDA = 4, 5


def i2c_wave(ops, name="i2c.csv"):
    """ops 是动作序列，见下面各用例。"""
    b = Builder()
    b.init(SCL, 1)
    b.init(SDA, 1)
    t = 10 * PERIOD

    def scl_cycle(bit):
        nonlocal t
        b.set(SDA, bit, t)                  # SCL 低电平时换数据
        t += PERIOD * 0.25
        b.set(SCL, 1, t)                    # 上升沿 → 从机采样
        t += PERIOD * 0.5
        b.set(SCL, 0, t)
        t += PERIOD * 0.25

    for op in ops:
        kind, arg = op[0], op[1:]
        if kind == "start":
            b.set(SDA, 1, t); t += PERIOD * 0.5
            b.set(SCL, 1, t); t += PERIOD * 0.5
            b.set(SDA, 0, t)                # SCL 高时 SDA 下降 = START
            t += PERIOD * 0.5
            b.set(SCL, 0, t); t += PERIOD * 0.5
        elif kind == "stop":
            b.set(SDA, 0, t); t += PERIOD * 0.25
            b.set(SCL, 0, t); t += PERIOD * 0.5
            b.set(SCL, 1, t); t += PERIOD * 0.5
            b.set(SDA, 1, t)                # SCL 高时 SDA 上升 = STOP
            t += PERIOD * 0.5
        elif kind == "byte":
            value, ack = arg
            for k in range(8):
                scl_cycle((value >> (7 - k)) & 1)
            scl_cycle(0 if ack else 1)      # ACK=低 / NACK=高
        elif kind == "idle":
            t += PERIOD
    b.t = t + PERIOD
    return b.wave(name)


# ---- 11. 完整写事务：START + 地址W + ACK + 数据 + ACK + STOP ----
w = i2c_wave([("start",), ("byte", 0xA0, True), ("byte", 0x12, True),
              ("byte", 0x34, True), ("stop",)])
r = wv.decode_i2c(w.by_index(SCL), w.by_index(SDA))
check("I2C：START / 地址 W / 2 数据 / STOP 全部识别",
      [i.kind for i in r.items] == ["start", "byte", "byte", "byte", "stop"],
      [(i.kind, hex(i.value)) for i in r.items])
addr = r.items[1]
check("I2C：首字节识别为地址 0x50 写 + ACK",
      addr.is_addr and addr.value == 0xA0 and addr.rw == "W" and addr.ack, addr)
check("I2C：数据字节为 0x12 / 0x34 且都 ACK",
      r.bytes_[1:] == bytes([0x12, 0x34]) and not r.nacks, r.bytes_.hex(" "))

# ---- 12. 读事务 + 最后一个字节 NACK（主机收完最后字节的典型行为）----
w = i2c_wave([("start",), ("byte", 0xA1, True), ("byte", 0x5A, True),
              ("byte", 0x3C, False), ("stop",)])
r = wv.decode_i2c(w.by_index(SCL), w.by_index(SDA))
check("I2C：地址读位 = R",
      r.items[1].is_addr and r.items[1].rw == "R" and r.items[1].value == 0xA1, r.items[1])
check("I2C：最后字节 NACK 被标出", len(r.nacks) == 1 and r.nacks[0].value == 0x3C, r.nacks)

# ---- 13. 无器件应答：地址后就是 NACK ----
w = i2c_wave([("start",), ("byte", 0xA0, False), ("stop",)])
r = wv.decode_i2c(w.by_index(SCL), w.by_index(SDA))
txt = "\n".join(wv.format_i2c_report(r, "ch4", "ch5"))
check("I2C：地址 NACK → 报告里点明无器件应答",
      "NACK" in txt and "无器件应答" in txt, txt)

# ---- 14. repeated START（写地址 → repeated START → 读地址）----
w = i2c_wave([("start",), ("byte", 0xA0, True),
              ("start",), ("byte", 0xA1, True), ("byte", 0x77, False), ("stop",)])
r = wv.decode_i2c(w.by_index(SCL), w.by_index(SDA))
check("I2C：repeated START 识别为 2 次传输",
      r.transfers == 2 and [i.kind for i in r.items].count("start") == 2, r.transfers)
check("I2C：repeated START 后首字节重新按地址解析（第二个地址是读）",
      r.items[3].is_addr and r.items[3].rw == "R", r.items[3])

# ---- 15. SDA/SCL 标反 → 识别不出 START（可判别，不静默）----
w = i2c_wave([("start",), ("byte", 0xA0, True), ("byte", 0x12, True), ("stop",)])
swapped = wv.decode_i2c(w.by_index(SDA), w.by_index(SCL))
check("SDA/SCL 标反 → 一个字节都解不出（不会假装解对）", not swapped.bytes_, swapped.items)
stxt = "\n".join(wv.format_i2c_report(swapped, "ch5", "ch4"))
check("SDA/SCL 标反 → 报告点明疑似标反 / 通道选错",
      "SDA/SCL 标反" in stxt and "通道选错" in stxt, stxt)

# ---- 16. 只有 START 没有 STOP（窗口截断）要给提示 ----
w = i2c_wave([("start",), ("byte", 0xA0, True), ("byte", 0x12, True)])
r = wv.decode_i2c(w.by_index(SCL), w.by_index(SDA))
txt = "\n".join(wv.format_i2c_report(r, "ch4", "ch5"))
check("I2C：缺 STOP 时给截断提示", "只有 START 没有 STOP" in txt, txt)

# ---- 17. 参数解析 ----
s = wv.parse_i2c_spec("scl=3,sda=4")
check("解析 I2C spec", s.scl == "3" and s.sda == "4", s)
s2 = wv.parse_i2c_spec("3,4")
check("解析 I2C 位置形式 '3,4'", s2.scl == "3" and s2.sda == "4", s2)
try:
    wv.parse_i2c_spec("scl=3"); ok = False
except ValueError:
    ok = True
check("I2C 缺 sda 报错", ok)

# ---- 18. SPI 报告文本可读 ----
w = spi_build(MOSI_TX, MISO_TX, cpol=0, cpha=0, cs=True, name="rep.csv")
rr = spi_decode(w, mode=0)
txt = "\n".join(wv.format_spi_report(rr, {"CLK": "ch0", "MOSI": "ch1", "MISO": "ch2"}))
check("SPI 报告含 mode/字节流/采样边沿数",
      "mode 0" in txt and "MOSI 字节流" in txt and "9F 00 12 AB" in txt, txt)

print("─" * 50)
print(f"{sum(results)}/{len(results)} 通过")
sys.exit(0 if all(results) else 1)
