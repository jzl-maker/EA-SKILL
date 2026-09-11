#!/usr/bin/env python
"""digital.csv 波形读取 / 通道统计 / 内置 UART、SPI、I2C 解码。

**为什么需要这个模块**

Logic 2 的 `export_data_table`（decoded.csv）对**二进制协议是有损的**：不可打印
字节被渲染成 `.`（0x2E），NUL 被渲染成字面量 `\\0`。真机踩过 —— 解码表里的
`EF 2E FF FF FF FF 2E 00` 看着像真实数据，实际 0x01 / 0x08 / 0x82 全被渲染成了 `.`，
据此得出过完全错误的结论。

`digital.csv` 记录的是**精确的跳变时刻**（每通道每个边沿一行），不经过任何渲染，
是唯一可信的字节来源。本模块只依赖标准库，直接消费 `export_raw_data_csv` 的产物，
在 Logic 2 解码表失真时是唯一的退路。
"""

from __future__ import annotations

import bisect
import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

# 常见标准波特率（用于把实测最小脉宽归一到最近的档位）
STANDARD_BAUDS = (
    1200, 2400, 4800, 9600, 14400, 19200, 28800, 38400, 57600, 76800,
    115200, 128000, 230400, 256000, 460800, 921600, 1500000, 2000000, 3000000,
)

# decoded.csv 有损判定：某列里 `.` 占非空白字符的比例超过它就告警
LOSSY_DOT_RATIO = 0.10


@dataclass
class Channel:
    """一个数字通道的跳变序列。

    edges 已去重保证**严格高低交替**：每项是 (边沿时刻, 边沿之后的电平)。
    首项是捕获起点处的初始电平，不是跳变，因此 `transitions == len(edges) - 1`。
    """
    name: str
    index: int                      # 语义通道号（优先取表头里的数字，取不到用列序）
    edges: list[tuple[float, int]] = field(default_factory=list)

    _times: list[float] | None = field(default=None, repr=False, compare=False)

    @property
    def times(self) -> list[float]:
        if self._times is None:
            self._times = [t for t, _ in self.edges]
        return self._times

    @property
    def edges_only(self) -> list[tuple[float, int]]:
        """只含**真实跳变**（不含表示捕获起点电平的 edges[0]）。

        SPI 取采样边沿、I2C 找 START/STOP 都必须用这个：时钟空闲电平恰好等于
        要找的电平时，把 edges[0] 当成跳变会凭空多出一个边沿。
        """
        return self.edges[1:]

    @property
    def transitions(self) -> int:
        return max(0, len(self.edges) - 1)

    @property
    def t0(self) -> float:
        return self.edges[0][0] if self.edges else 0.0

    def level_at(self, t: float) -> int:
        """t 时刻的电平（含恰好在 t 发生的跳变）。超出末尾时外推。

        外推是刻意的：捕获末尾的半帧若被截断，外推会把停止位读成低电平 →
        报 framing error，正是想要的结论。
        """
        if not self.edges:
            return 0
        k = bisect.bisect_right(self.times, t) - 1
        return self.edges[k][1] if k >= 0 else self.edges[0][1]

    def level_before(self, t: float) -> int:
        """t 时刻**之前**的电平（不含恰好在 t 发生的跳变）。

        SPI 在一个时钟边沿采样数据时用的就是它：数据在**另一个**边沿翻转，
        所以采样边沿之前那一瞬必然已稳定，且对 CPHA=0/1 都成立。
        比"回退 0.1 个周期"精确，也不依赖周期估计。
        """
        if not self.edges:
            return 0
        k = bisect.bisect_left(self.times, t) - 1
        return self.edges[k][1] if k >= 0 else self.edges[0][1]

    def high_ratio(self, t_end: float) -> float:
        """高电平**按时长加权**的占比。

        注意不能按行数算：digital.csv 只在跳变处记录，行数与电平持续时间无关，
        行占比没有任何物理意义。
        """
        ts = self.times
        if len(ts) < 2:
            return 1.0 if self.edges and self.edges[0][1] else 0.0
        total = high = 0.0
        for i, (t, v) in enumerate(self.edges):
            nxt = ts[i + 1] if i + 1 < len(ts) else t_end
            d = nxt - t
            if d <= 0:
                continue
            total += d
            if v:
                high += d
        return high / total if total > 0 else 0.0

    def min_pulse(self) -> float | None:
        """最短脉宽。UART 里起始位恒为 1 bit，所以它 ≈ 1 bit 时长。"""
        ts = self.times
        widths = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
        widths = [w for w in widths if w > 0]
        return min(widths) if widths else None

    def estimate_baud(self) -> tuple[float, float, float] | None:
        """由最短脉宽粗估波特率 → (实测, 最接近的标准档, 相对偏差)。"""
        mp = self.min_pulse()
        if not mp or mp <= 0:
            return None
        raw = 1.0 / mp
        near = min(STANDARD_BAUDS, key=lambda b: abs(b - raw) / b)
        return raw, near, abs(near - raw) / near


@dataclass
class Waveform:
    path: Path
    t_start: float
    t_end: float
    rows: int
    channels: list[Channel]

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start

    def by_index(self, n: int) -> Channel | None:
        """按语义通道号取通道（--channels 里写的号）。"""
        for ch in self.channels:
            if ch.index == n:
                return ch
        return None

    def resolve(self, spec: str) -> Channel | None:
        """把 'ch7' / '7' / 'Channel 7' 解析成通道。"""
        m = re.search(r"(\d+)", str(spec))
        if not m:
            return None
        return self.by_index(int(m.group(1)))


def load_digital_csv(path: str | Path) -> Waveform:
    """解析 Logic 2 `export_raw_data_csv` 产出的 digital.csv。

    格式：第一行表头 `Time [s],Channel 0,Channel 1,...`；此后**仅在任意通道发生
    跳变时**才写一行，行内含所有通道当时的电平。
    """
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f)
        header = next(rd, None)
        if not header or len(header) < 2:
            raise ValueError(f"{path.name} 表头异常（需要 时间列 + 至少一个通道列）")

        header = [h.strip() for h in header]
        names: list[str] = []
        indexes: list[int] = []
        for k, nm in enumerate(header[1:]):
            names.append(nm or f"Channel {k}")
            m = re.search(r"(\d+)", nm or "")
            indexes.append(int(m.group(1)) if m else k)

        last: list[int | None] = [None] * len(names)
        edges: list[list[tuple[float, int]]] = [[] for _ in names]
        rows = 0
        t_start = t_end = None

        for row in rd:
            if len(row) < len(names) + 1:
                continue
            try:
                t = float(row[0])
            except ValueError:
                continue
            rows += 1
            if t_start is None:
                t_start = t
            t_end = t
            for k in range(len(names)):
                try:
                    v = 1 if float(row[k + 1]) != 0 else 0
                except (TypeError, ValueError):
                    continue                        # 空单元格/脏值：保持上一电平
                if last[k] == v:
                    continue
                edges[k].append((t, v))
                last[k] = v

    if t_start is None:
        raise ValueError(f"{path.name} 没有有效数据行")
    channels = [Channel(name=names[k], index=indexes[k], edges=edges[k])
                for k in range(len(names))]
    return Waveform(path=path, t_start=t_start, t_end=t_end, rows=rows,
                    channels=channels)


# ─────────────────────────── 通道素描（问题 5） ───────────────────────────

def describe_channel(ch: Channel, t_end: float) -> str:
    """单通道素描：跳变数 / 高位占比 / 最短脉宽 / 波特率粗估。

    直接回答"ch7 到底是 TX 还是有信号"—— 此前只能靠人工数跳变，判错过。
    """
    ratio = ch.high_ratio(t_end)
    head = f"  ch{ch.index:<3}"
    if ch.transitions == 0:
        return (f"{head} 跳变 0        {'恒定高' if ratio > 0.5 else '恒定低'}"
                f"          —              （无信号）")
    baud = ch.estimate_baud()
    baud_txt = f"{baud[1]} (实测 {baud[0]:.0f})" if baud else "—"
    mp = ch.min_pulse()
    pulse_txt = f"{mp*1e6:8.2f}µs" if mp else "       —"
    return (f"{head} 跳变 {ch.transitions:<8} 高位占比 {ratio*100:5.1f}%  "
            f"最短脉宽 {pulse_txt}  疑似波特率 {baud_txt}")


# ─────────────────────────── 内置 UART 解码（问题 2） ───────────────────────────

@dataclass
class Frame:
    t: float
    value: int
    ok: bool
    error: str = ""          # "" | "framing" | "parity"


@dataclass
class UartResult:
    channel: int
    baud: float
    data_bits: int
    parity: str
    stop_bits: float
    frames: list[Frame]

    @property
    def good(self) -> list[Frame]:
        return [f for f in self.frames if f.ok]

    @property
    def errors(self) -> list[Frame]:
        return [f for f in self.frames if not f.ok]

    @property
    def bytes_(self) -> bytes:
        return bytes(f.value for f in self.good)


def decode_uart(ch: Channel, baud: float, *, data_bits: int = 8, parity: str = "none",
                stop_bits: float = 1.0) -> UartResult:
    """从跳变序列解 UART（默认 8N1），返回**精确字节**。

    防误同步靠三条，缺一条就会在数据位里重新同步、吐出成片伪帧（真机手写解码器
    时就是这么踩进去的）：

    1. **起始位前须有 ≥0.5 bit 空闲高电平** —— 滤掉紧跟窄高脉冲的下降沿；
    2. **起始位中心（t+0.5 bit）仍须为低** —— 滤掉长空闲之后的亚比特毛刺；
    3. **每接受一帧就整帧跳到帧尾之后** —— 帧内的数据位下降沿根本不会被重新审视。
       这条最要紧：单靠前两条挡不住"数据位上恰好有整比特高电平"的情况（如 0x02
       的 d1=1、d2=0，其下降沿前面确实有 1 bit 高电平）。

    只在捕获起点已知空闲时才能起解；捕获开头就是下降沿时（初始电平为低）不做
    猜测，跳过 —— 没有证据证明前面在空闲。
    """
    result = UartResult(channel=ch.index, baud=baud, data_bits=data_bits,
                        parity=parity, stop_bits=stop_bits, frames=[])
    if baud <= 0:
        return result

    edges = ch.edges
    if len(edges) < 2:
        return result
    ts = ch.times
    bit = 1.0 / baud
    par_bits = 0 if parity in ("none", "", None) else 1
    frame_bits = 1 + data_bits + par_bits + float(stop_bits)

    i = 0
    n = len(edges)
    while i < n:
        t, lvl = edges[i]
        if lvl != 0:
            i += 1
            continue
        # 判据 1：前面必须已有 ≥0.5 bit 的空闲高电平
        if i == 0 or edges[i - 1][1] != 1 or (t - edges[i - 1][0]) < 0.5 * bit:
            i += 1
            continue
        # 判据 2：起始位中心仍须为低 —— 长空闲之后的亚比特毛刺过不了这一关
        if ch.level_at(t + 0.5 * bit) != 0:
            i += 1
            continue

        value = 0
        for k in range(data_bits):
            if ch.level_at(t + (1.5 + k) * bit):
                value |= 1 << k
        stop = ch.level_at(t + (1.5 + data_bits + par_bits) * bit)
        err = ""
        if stop != 1:
            err = "framing"
        elif par_bits:
            ones = bin(value).count("1") & 1
            p = ch.level_at(t + (1.5 + data_bits) * bit)
            if (parity == "even" and p != ones) or (parity == "odd" and p != (1 - ones)):
                err = "parity"
        result.frames.append(Frame(t=t, value=value, ok=not err, error=err))

        # 判据 3：整帧跳过，绝不在数据位里重新同步。
        # 截断点取数据区末端再往回让 0.5 bit，而不是精确的帧尾：
        #   - 帧内最后一个可能出现的下降沿在 t + data_bits*bit（最后一个数据位的起点），
        #     距数据区末端还有 parity+stop 个比特，所以回让 0.5 bit 是安全的；
        #   - 而按精确帧尾 t + frame_bits*bit 截断时，浮点误差会让算出的时刻比下一个
        #     起始位边沿早/晚 1 ULP，bisect_left 一旦跳过它，整条流就此失步。
        cut = t + (1 + data_bits + par_bits) * bit - 0.5 * bit
        i = max(i + 1, bisect.bisect_left(ts, cut))
    return result


def format_hex_rows(data: bytes, per_row: int = 16) -> list[str]:
    """把字节流排成带偏移的 hex 行，便于和协议文档对拍。"""
    out = []
    for off in range(0, len(data), per_row):
        chunk = data[off:off + per_row]
        hexs = " ".join(f"{b:02X}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"    {off:08X}  {hexs:<{per_row * 3}}  {text}")
    return out


def swap_hint(tx: UartResult | None, rx: UartResult | None) -> str | None:
    """TX/RX 通道标注可能接反时的提示。

    判据：谁**先开口**。协议里主动方（TX 侧）必然先发，所以如果被标成 RX 的
    通道比分到 TX 的通道更早出帧，标注多半反了。
    """
    if not tx or not rx:
        return None
    a, b = tx.good, rx.good
    if not a or not b:
        return None
    if b[0].t + 1e-9 < a[0].t:
        return (f"⚠️  被标为 RX 的 ch{rx.channel} 比 TX 的 ch{tx.channel} 先开口"
                f"（{b[0].t:.6f}s < {a[0].t:.6f}s）—— 主动方先发，疑似 TX/RX 标注接反")
    return None


# ─────────────────────────── 内置 SPI 解码 ───────────────────────────

# 无 CS 时，相邻采样边沿间隔超过 N 个时钟周期即认为换了一帧
SPI_BURST_GAP = 3.0

SPI_MODE_NAMES = {0: "mode 0 (CPOL=0 CPHA=0)", 1: "mode 1 (CPOL=0 CPHA=1)",
                  2: "mode 2 (CPOL=1 CPHA=0)", 3: "mode 3 (CPOL=1 CPHA=1)"}


@dataclass
class SpiWord:
    t: float
    mosi: int | None
    miso: int | None


@dataclass
class SpiResult:
    mode: int
    detection: str                  # mode 的判定依据（给人看的文案）
    bits: int
    order: str
    clk_period: float | None
    words: list[SpiWord]
    ragged: int                     # 凑不满一个字的采样数（时钟毛刺/模式不对的信号）
    frames: int
    guessed_cpha: bool = False      # mode 未显式给出、CPHA 按 0 猜的

    def stream(self, which: str) -> list[int]:
        """该信号的逐字取值（mosi / miso）。bits>8 时也成立，故不返回 bytes。"""
        return [getattr(w, which) for w in self.words
                if getattr(w, which) is not None]


def _median_gap(times: list[float]) -> float | None:
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return None
    gaps.sort()
    return gaps[len(gaps) // 2]


def _sample_word(ch: Channel | None, times: list[float], order: str) -> int | None:
    """在给定的时钟边沿上采一个字的各位。"""
    if ch is None:
        return None
    value = 0
    n = len(times)
    for k, t in enumerate(times):
        if ch.level_before(t):
            value |= 1 << (k if order == "lsb" else n - 1 - k)
    return value


def _resolve_spi_mode(clk: Channel, cs: Channel | None,
                      mode: int | None) -> tuple[int, int, str, bool]:
    """返回 (cpol, cpha, 判定依据, 是否猜的)。mode 给 None 则只自检 CPOL、CPHA 取 0。

    CPOL 就是"事务之间时钟停在哪一档"。CS 存在时用它最可靠（CS 拉高的瞬间
    时钟必然处于帧间空闲）；否则退而用最后一个时钟边沿之后的电平。

    第 4 个返回值是**控制流用的**，不能拿 detection 文案去 startswith 判断：
    改一个字的措辞就会让下游提示静默消失。
    """
    if mode is not None:
        if not 0 <= mode <= 3:
            raise ValueError(f"SPI mode 只能是 0..3，收到 {mode}")
        return mode >> 1, mode & 1, "显式指定", False

    if cs is not None:
        for t, v in cs.edges_only:
            if v == 1:                      # CS 拉高 → 帧间空闲
                return clk.level_at(t), 0, "按 CS 拉高时的时钟电平自检", True

    if clk.edges_only:
        return clk.edges_only[-1][1], 0, "按最后一个时钟边沿之后的电平自检", True

    cpol = clk.edges[0][1] if clk.edges else 0
    return cpol, 0, "无时钟跳变，按捕获起点电平猜测", True


def _spi_groups(sampling: list[float], cs: Channel | None,
                period: float | None) -> list[list[float]]:
    """把采样边沿分段 —— 每段是"一个字或几个字"的时钟串。

    有 CS 就按 CS 拉低区间分段（最可靠）；没有就按时钟间隙切。

    分段必须走 CS 自己的跳变划分窗口，**不能**靠"遍历采样边沿看 CS 电平"来切：
    CS 拉高期间根本没有采样边沿，那种写法只会得到"一串连续的低电平"、永远不切段。
    """
    if not sampling:
        return []
    if cs is not None:
        lows: list[tuple[float, float]] = []
        start = None
        for t, v in cs.edges:               # edges[0] 是捕获起点电平，一并参与
            if v == 0 and start is None:
                start = t
            elif v == 1 and start is not None:
                lows.append((start, t))
                start = None
        if start is not None:               # CS 一直低到捕获结束
            lows.append((start, float("inf")))
        groups: list[list[float]] = []
        for a, b in lows:
            i = bisect.bisect_left(sampling, a)
            j = bisect.bisect_left(sampling, b)
            if j > i:
                groups.append(sampling[i:j])
        return groups

    if not period:
        return [sampling]
    groups, cur = [], []
    prev = None
    for t in sampling:
        if prev is not None and (t - prev) > period * SPI_BURST_GAP:
            groups.append(cur)
            cur = []
        cur.append(t)
        prev = t
    if cur:
        groups.append(cur)
    return groups


def decode_spi(clk: Channel, mosi: Channel | None = None,
               miso: Channel | None = None, cs: Channel | None = None,
               mode: int | None = None, bits: int = 8,
               order: str = "msb") -> SpiResult:
    """从跳变序列解 SPI，返回每个字的 MOSI/MISO 字节。

    采样边沿由 (CPOL, CPHA) 决定：`want_level = 1 if CPOL == CPHA else 0`
    （CPHA=0 取前沿，CPHA=1 取后沿）。数据用 `level_before` 取 —— 数据在另一个
    边沿翻转，所以采样边沿之前那一瞬对四种模式都已稳定。
    """
    cpol, cpha, why, guessed = _resolve_spi_mode(clk, cs, mode)
    want = 1 if cpol == cpha else 0
    sampling = [t for t, v in clk.edges_only if v == want]

    period = _median_gap(sampling)
    groups = _spi_groups(sampling, cs, period)

    words: list[SpiWord] = []
    ragged = 0
    for g in groups:
        for i in range(0, len(g), bits):
            chunk = g[i:i + bits]
            if len(chunk) < bits:
                ragged += len(chunk)        # 段尾凑不满一个字
                break
            words.append(SpiWord(t=chunk[0],
                                 mosi=_sample_word(mosi, chunk, order),
                                 miso=_sample_word(miso, chunk, order)))
    return SpiResult(mode=(cpol << 1) | cpha, detection=why, bits=bits,
                     order=order, clk_period=period, words=words,
                     ragged=ragged, frames=len(groups), guessed_cpha=guessed)


def format_spi_report(res: SpiResult, labels: dict[str, str]) -> list[str]:
    """SPI 解码结果 → 给人看的多行文本。"""
    who = " ".join(f"{k.upper()}={v}" for k, v in labels.items())
    clk = f"，时钟 ≈{1/res.clk_period/1e6:.3f} MHz" if res.clk_period else ""
    out = [f"\n  🔎 内置 SPI 解码（{who}，{SPI_MODE_NAMES[res.mode]}，"
           f"{res.bits} bit {'LSB' if res.order == 'lsb' else 'MSB'} first{clk}）",
           f"     mode 判定：{res.detection}；共 {res.frames} 段 / {len(res.words)} 个字"]
    hexw = max(2, (res.bits + 3) // 4)      # 16 bit 字要按 4 位十六进制打印
    if res.words:
        cols = [c for c in ("mosi", "miso") if any(
            getattr(w, c) is not None for w in res.words)]
        out.append("       #   t(s)              " +
                   "  ".join(c.upper().ljust(hexw + 2) for c in cols))
        for i, w in enumerate(res.words):
            cells = "  ".join(f"0x{getattr(w, c):0{hexw}X}" for c in cols)
            out.append(f"    {i:>4}   {w.t:<16.9f}  {cells}")
        for c in cols:
            out.append(f"     {c.upper()} 字节流："
                       + " ".join(f"{v:0{hexw}X}" for v in res.stream(c)))
    if res.ragged:
        out.append(f"    ⚠️  有 {res.ragged} 个采样边沿凑不满一个字 —— "
                   f"可能是 mode 猜错、CS/时钟接线不对，或时钟上有毛刺")
    if res.guessed_cpha:
        out.append("    提示：CPHA 默认按 0 处理；若时序对不上请显式给 mode=1/2/3")
    return out


# ─────────────────────────── 内置 I2C 解码 ───────────────────────────

@dataclass
class I2cItem:
    kind: str                       # "start" | "stop" | "byte"
    t: float
    value: int = 0
    is_addr: bool = False
    rw: str = ""
    ack: bool = True


@dataclass
class I2cResult:
    items: list[I2cItem]

    @property
    def bytes_(self) -> bytes:
        return bytes(i.value for i in self.items if i.kind == "byte")

    @property
    def nacks(self) -> list[I2cItem]:
        return [i for i in self.items if i.kind == "byte" and not i.ack]

    @property
    def transfers(self) -> int:
        return sum(1 for i in self.items if i.kind == "start")


def decode_i2c(scl: Channel, sda: Channel) -> I2cResult:
    """从跳变序列解 I2C（7 位地址）。

    规则：SCL 为高时 SDA 的跳变是 START（下降）或 STOP（上升）；数据在 SCL
    上升沿采样，第 9 位是 ACK（低）/ NACK（高）。
    """
    events: list[tuple[float, int, int]] = []       # (t, kind, 电平)，kind 0=SCL 1=SDA
    events += [(t, 0, v) for t, v in scl.edges_only]
    events += [(t, 1, v) for t, v in sda.edges_only]
    # 同一时刻优先按 SDA 处理：SCL 上的电平查询要看到"数据先变"之前的状态
    events.sort(key=lambda e: (e[0], -e[1]))

    items: list[I2cItem] = []
    values: list[int] = []              # 本字节已采样的位（含 ACK 位）
    started = False
    first_byte = True

    for t, kind, v in events:
        if kind == 1:                   # SDA 跳变
            if scl.level_before(t) == 1:
                # SCL 为高时的 SDA 跳变 = START/STOP（用 level_before 排除
                # "SCL 恰好在同一刻上升"的巧合，那不是 START）
                if v == 0:
                    items.append(I2cItem("start", t))
                    started, first_byte = True, True
                    values = []
                elif started:
                    items.append(I2cItem("stop", t))
                    started, first_byte = False, True
                    values = []
            continue

        if v != 1 or not started:       # 只在 SCL 上升沿采样
            continue
        values.append(sda.level_before(t))
        if len(values) < 9:             # 8 位数据 + 1 位 ACK
            continue

        value = 0
        for k, b in enumerate(values[:8]):
            value |= b << (7 - k)       # I2C 定死 MSB first
        ack = values[8] == 0
        if first_byte:
            items.append(I2cItem("byte", t, value=value, is_addr=True,
                                 rw=("R" if value & 1 else "W"), ack=ack))
            first_byte = False
        else:
            items.append(I2cItem("byte", t, value=value, ack=ack))
        values = []
    return I2cResult(items)


def format_i2c_report(res: I2cResult, scl_label: str, sda_label: str) -> list[str]:
    """I2C 解码结果 → 给人看的多行文本。"""
    nbytes = len([i for i in res.items if i.kind == "byte"])
    head = (f"\n  🔎 内置 I2C 解码（SCL={scl_label} SDA={sda_label}）："
            f"{res.transfers} 次传输 / {nbytes} 字节")
    if res.nacks:
        head += f"，{len(res.nacks)} 个 NACK"
    out = [head]
    for i in res.items:
        if i.kind == "start":
            out.append(f"     t={i.t:<16.9f}  START")
        elif i.kind == "stop":
            out.append(f"     t={i.t:<16.9f}  STOP")
        elif i.is_addr:
            out.append(f"     t={i.t:<16.9f}  {i.value:02X} {i.rw}  "
                       f"{'ACK' if i.ack else 'NACK'}     "
                       f"← 地址 0x{i.value >> 1:02X} {'读' if i.rw == 'R' else '写'}"
                       + ("（无器件应答，检查接线/地址）" if not i.ack else ""))
        else:
            out.append(f"     t={i.t:<16.9f}  {i.value:02X}    "
                       f"{'ACK' if i.ack else 'NACK'}")
    if nbytes:
        out.append("     字节流：" + " ".join(f"{b:02X}" for b in res.bytes_))
    if res.items and not any(i.kind == "stop" for i in res.items):
        out.append("    提示：捕获窗口内只有 START 没有 STOP —— 可能是被截断，"
                   "或 SDA/SCL 标反了（标反时通常连 START 都识别不出来）")
    elif res.items and nbytes == 0:
        # SDA/SCL 接反的典型特征：两边都是时钟样波形，于是解出一堆 START/STOP，
        # 但一条数据位都凑不齐 —— 属于"看着有输出、其实全不对"，必须点明。
        out.append("    提示：有 START/STOP 却解不出任何字节 —— 极可能 SDA/SCL 标反了"
                   "（或通道选错）。核对 --decode-i2c 的两个通道号，"
                   "并看通道素描里哪条更像时钟（跳变多、占空比接近 50%）")
    return out


# ─────────────────────── decoded.csv 有损扫描（问题 1） ───────────────────────

def scan_decoded_csv(path: str | Path) -> dict:
    """检查 Logic 2 解码表是否把二进制数据渲染丢了。

    Logic 2 把不可打印字节渲染成 `.`、NUL 渲染成字面量 `\\0`，因此数据列里
    成片的 `.` 极可能不是真的 0x2E。返回每列的统计与最可疑的列。
    """
    path = Path(path)
    report: dict = {"path": str(path), "rows": 0, "columns": {}, "lossy": False,
                    "worst": None, "worst_ratio": 0.0}
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            rd = csv.reader(f)
            header = next(rd, None)
            if not header:
                return report
            header = [h.strip() for h in header]
            total = [0] * len(header)       # 非空白字符数
            dots = [0] * len(header)        # '.' 数
            nuls = [0] * len(header)        # 含 NUL 渲染的单元格数
            cells = [0] * len(header)
            for row in rd:
                report["rows"] += 1
                for k in range(min(len(row), len(header))):
                    cell = row[k].strip()
                    if not cell:
                        continue
                    cells[k] += 1
                    body = cell.replace(" ", "")
                    total[k] += len(body)
                    dots[k] += body.count(".")
                    if "\\0" in cell or "\x00" in cell:
                        nuls[k] += 1
    except OSError:
        return report

    for k, name in enumerate(header):
        if cells[k] < 3 or total[k] == 0:
            continue
        ratio = dots[k] / total[k]
        report["columns"][name] = {"cells": cells[k], "dot_ratio": round(ratio, 4),
                                   "nul_cells": nuls[k]}
        # 只看第 2 列起（第 1 列通常是时间，不会有点）
        if k > 0 and ratio > report["worst_ratio"]:
            report["worst_ratio"] = ratio
            report["worst"] = name
    report["lossy"] = report["worst_ratio"] >= LOSSY_DOT_RATIO
    return report


def format_lossy_warning(rep: dict) -> list[str]:
    """把有损扫描结果转成给用户看的告警行（无问题返回空列表）。"""
    if not rep.get("lossy"):
        return []
    worst = rep["worst"]
    col = rep["columns"][worst]
    lines = [
        f"  ⚠️  decoded.csv 的 {worst!r} 列有 {col['dot_ratio']*100:.0f}% 的字符是 '.'"
        f"（{rep['rows']} 行）",
        "      Logic 2 把不可打印字节渲染成 '.'（0x2E）、NUL 渲染成字面量 '\\0'，",
        "      这一列不能当字节值用。要精确字节请加 --export-raw 后用 --decode-uart，",
        "      或自行从 digital.csv 解。",
    ]
    if col["nul_cells"]:
        lines.append(f"      其中 {col['nul_cells']} 个单元格含 NUL 渲染（'\\0'）。")
    return lines


# ─────────────────────────── --decode-* 参数解析 ───────────────────────────

def _parse_pairs(spec: str) -> dict[str, str]:
    """`"clk=0,mosi=1"` → `{'clk': '0', 'mosi': '1'}`；无名项依次收成 `_pos0`/`_pos1`。"""
    out: dict[str, str] = {}
    pos = 0
    for pair in str(spec).split(","):
        pair = pair.strip()
        if not pair:
            continue
        key, sep, val = pair.partition("=")
        if sep:
            out[key.strip().lower()] = val.strip()
        else:
            out[f"_pos{pos}"] = pair
            pos += 1
    return out


@dataclass
class UartSpec:
    channels: dict[str, str]        # {'TX': '7', 'RX': '15'} 或 {'ch': '7'}
    baud: float | None              # None == auto
    data_bits: int = 8
    parity: str = "none"
    stop_bits: float = 1.0


def parse_uart_spec(spec: str) -> UartSpec:
    """解析 `--decode-uart "ch=7,baud=57600"` / `"TX=7,RX=15,baud=auto"`。

    位置形式 `"7,57600"` 也接受（第一个数字是通道、第二个是波特率）。
    """
    channels: dict[str, str] = {}
    baud: float | None = None
    data_bits, parity, stop_bits = 8, "none", 1.0
    positional: list[str] = []

    for pair in str(spec).split(","):
        pair = pair.strip()
        if not pair:
            continue
        key, sep, val = pair.partition("=")
        if not sep:
            positional.append(pair)
            continue
        key, val = key.strip().lower(), val.strip()
        if key in ("ch", "channel"):
            channels.setdefault("ch", val)
        elif key in ("tx", "rx"):
            channels[key.upper()] = val
        elif key == "baud":
            baud = None if val.lower() in ("auto", "") else float(val)
        elif key in ("bits", "data", "data_bits"):
            data_bits = int(val)
        elif key == "parity":
            parity = val.lower()
        elif key == "stop":
            stop_bits = float(val)

    if not channels and positional:
        channels["ch"] = positional[0]
        if len(positional) > 1 and baud is None:
            baud = float(positional[1])
    if not channels:
        raise ValueError(f"--decode-uart 缺少通道：{spec!r}（例：ch=7,baud=57600）")
    return UartSpec(channels=channels, baud=baud, data_bits=data_bits,
                    parity=parity, stop_bits=stop_bits)


# ─────────────────────── --decode-spi / --decode-i2c 参数解析 ───────────────────────

@dataclass
class SpiSpec:
    signals: dict[str, str]         # CLK/MOSI/MISO/CS → 通道号
    mode: int | None = None         # None == auto（自检 CPOL，CPHA 取 0）
    bits: int = 8
    order: str = "msb"


def parse_spi_spec(spec: str) -> SpiSpec:
    """解析 `--decode-spi "clk=0,mosi=1,miso=2,cs=3,mode=0"`。

    至少要给 clk 和 mosi/miso 之一；cs 可选（给了就能按片选切帧，最可靠）。
    mode 不给则自检 CPOL（CPHA 按 0）。
    """
    signals: dict[str, str] = {}
    mode: int | None = None
    bits, order = 8, "msb"
    for key, val in _parse_pairs(spec).items():
        if key in ("clk", "sclk", "sck", "clock"):
            signals["CLK"] = val
        elif key in ("mosi", "sdi", "copi"):
            signals["MOSI"] = val
        elif key in ("miso", "sdo", "cipo"):
            signals["MISO"] = val
        elif key in ("cs", "ss", "nss", "csn"):
            signals["CS"] = val
        elif key == "mode":
            mode = None if val.lower() == "auto" else int(val)
        elif key == "bits":
            bits = int(val)
        elif key in ("order", "msb", "lsb"):
            order = val.strip().lower()
        else:
            raise ValueError(f"--decode-spi 不认识 {key!r}（可用 clk/mosi/miso/cs/mode/bits/order）")

    if "CLK" not in signals:
        raise ValueError(f"--decode-spi 缺少 clk：{spec!r}（例：clk=0,mosi=1,miso=2,cs=3）")
    if "MOSI" not in signals and "MISO" not in signals:
        raise ValueError(f"--decode-spi 至少要给 mosi 或 miso：{spec!r}")
    if mode is not None and not 0 <= mode <= 3:
        raise ValueError(f"SPI mode 只能是 0..3（或 auto），收到 {mode}")
    if order not in ("msb", "lsb"):
        raise ValueError(f"order 只能是 msb/lsb，收到 {order!r}")
    if bits < 1:
        raise ValueError(f"bits 必须 ≥1，收到 {bits}")
    return SpiSpec(signals=signals, mode=mode, bits=bits, order=order)


@dataclass
class I2cSpec:
    scl: str
    sda: str


def parse_i2c_spec(spec: str) -> I2cSpec:
    """解析 `--decode-i2c "scl=3,sda=4"`（位置形式 `"3,4"` 也可）。"""
    pairs = _parse_pairs(spec)
    scl = pairs.get("scl") or pairs.get("sck") or pairs.get("clk")
    sda = pairs.get("sda") or pairs.get("data")
    if not scl or not sda:
        positional = [v for k, v in pairs.items() if k.startswith("_pos")]
        if len(positional) >= 2 and not scl and not sda:
            scl, sda = positional[0], positional[1]
    if not scl or not sda:
        raise ValueError(f"--decode-i2c 需要 scl 和 sda：{spec!r}（例：scl=3,sda=4）")
    return I2cSpec(scl=scl, sda=sda)
