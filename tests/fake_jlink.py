# -*- coding: utf-8 -*-
"""Fake JLink.exe：模拟 -CommanderScript 的 savebin / w4，无硬件验证 RTT 采集链路。

关键是要**忠实模拟目标侧的环形缓冲语义**（单消费者 + 写满即丢），否则测不出
"读取端不推进 RdOff" 这个缺陷：

  - 每次脚本执行前写一行新日志（模拟"两次采样之间目标一直在打印"）
  - 空间不够就**整条丢弃**、WrOff 原地不动（NO_BLOCK_SKIP）—— 即 WrOff 冻住的现象
  - 只有 w4 写回 RdOff 之后空间才释放，后续日志才续得上

闪存模拟（供 flash-jlink 回读校验用）：`loadbin` 写入、`savebin` 读回，等同于一块
Flash。**故意不解析 .hex** —— 若由 fake 复用被测代码的解析器，解析错了会两边一起错，
比对必然"通过"，等于没测。

环境变量：
  FAKE_UP_SIZE    环形缓冲大小（默认 0x800）
  FAKE_RTT_FLAGS  aUp[0].Flags（0=NO_BLOCK_SKIP 2=BLOCK_IF_FIFO_FULL）
  FAKE_RD_FOLLOW  1=模拟另一 RTT 读取端始终把 rd 追到 wr（直到我们 w4 接管）
  FAKE_IGNORE_W4  1=模拟写回不生效（目标拒绝写 / RTT 被重新初始化）
  FAKE_STATE      状态文件路径（默认同目录 state.json）；测试应各自指定避免串扰
  FAKE_FLASH_CORRUPT 1=回读时翻掉一个字节 → 模拟**真失败**
  FAKE_SELF_VERIFY_FAIL 1=输出里打印 "Verification failed" → 模拟 J-Link 自校验**误报**
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("FAKE_STATE") or os.path.join(HERE, "state.json")

CB_ADDR = 0x200014DC
UP_ADDR = 0x20001554
RD_OFF = CB_ADDR + 40                    # aUp[0].RdOff 在控制块里的偏移
UP_SIZE = int(os.environ.get("FAKE_UP_SIZE", "0x800"), 0)
FLAGS = int(os.environ.get("FAKE_RTT_FLAGS", "0"))
IGNORE_W4 = os.environ.get("FAKE_IGNORE_W4") == "1"

# Flash 状态单独一个文件：混进 state.json 就得改 save_state 的签名，
# 而 RTT 用例都在调它 —— 为加测试去动被测用例的公共路径不划算。
FLASH_STATE = STATE + ".flash"
READ_COUNT = FLASH_STATE + ".reads"     # 累计被回读了几次（跨进程，靠文件）
FLASH_CORRUPT = os.environ.get("FAKE_FLASH_CORRUPT") == "1"
SELF_VERIFY_FAIL = os.environ.get("FAKE_SELF_VERIFY_FAIL") == "1"
# 模拟实机行为：**紧接着一次编程之后**的首次回读整幅错位 +8 字节，再读就正常
# （N32G4FR + J-Link V7.94b 实测，`r`+`g` 不触发、只有编程触发）
SHIFT_FIRST = os.environ.get("FAKE_SHIFT_FIRST") == "1"
# 每次回读都错位**不同的**量 → 永远稳定不下来，工具必须判"无法定论"而非"真失败"
UNSTABLE = os.environ.get("FAKE_UNSTABLE") == "1"
# **实机实测到的完整序列**：错位 → 陈旧值 → **同一个陈旧值**（两次一模一样）→ 正确…
# 这是最阴的一段：只要求"连续两次相同"的判定会锁死在那对陈旧值上。故单独建模。
STALE_PLATEAU = os.environ.get("FAKE_STALE_PLATEAU") == "1"


def cnum(tok):
    """savebin 的地址/长度一律按**十六进制**解析，裸数字也一样。

    实测：`savebin f, 0x08000000, 106104` 里裸的 106104 被 Commander 当成 0x106104，
    读穿整片 Flash 拿回 512KB。若这里图省事写 int(tok, 0)（裸数字=十进制），fake 就比
    真硬件宽容，恰好在被测代码唯一出错的地方"帮它一把"，缺陷永远测不出来。

    只对 savebin 的参数这么处理 —— `speed 4000` 那类是十进制，别照搬。
    """
    return int(tok, 16)


def load_flash():
    """返回 `[[起始地址, 字节hex], ...]`。"""
    if os.path.exists(FLASH_STATE):
        with open(FLASH_STATE) as f:
            return [[int(a, 16), b] for a, b in json.load(f)]
    return []


def save_flash(regions):
    with open(FLASH_STATE, "w") as f:
        json.dump([[hex(a), b] for a, b in regions], f)


def flash_read(regions, addr, size):
    """回读 `[addr, addr+size)`；未被烧录内容覆盖处填 0xFF（擦除态）。

    完全没命中任何已烧录区域时返回 None，交回调用方走通用数据分支 ——
    否则 RTT 那些 savebin 会被这里"读到全 0xFF"静默接管。
    """
    out = bytearray(b"\xFF" * size)
    hit = False
    for r_addr, r_hex in regions:
        rb = bytes.fromhex(r_hex)
        lo, hi = max(addr, r_addr), min(addr + size, r_addr + len(rb))
        if lo < hi:
            out[lo - addr:hi - addr] = rb[lo - r_addr:hi - r_addr]
            hit = True
    if not hit:
        return None
    if FLASH_CORRUPT:
        out[1] ^= 0xFF               # 翻第 1 字节：错开首字节，避免"第一字节不同"掩盖实现细节
    elif UNSTABLE:
        # 每次翻**不同位置**的字节。早先用"错位 8*n 字节"建模，在 16 字节的小缓冲上
        # 第 2 次起就全饱和成 0x00 —— 于是第 3、4 次彼此"一致"，反被工具当成已稳定，
        # 用例失去意义。改成按次数翻不同下标，与缓冲大小无关，永远稳定不下来。
        n = bump_reads()
        out[(n - 1) % size] ^= 0xFF
    elif SHIFT_FIRST:
        if bump_reads() == 1:
            # 前置 8 字节 → 整幅后移，正是实机看到的 sh[8:] == want[:-8]
            out = bytearray(b"\x00" * 8 + bytes(out))[:size]
    elif STALE_PLATEAU:
        n = bump_reads()
        if n == 1:                      # 第 1 次：错位镜像
            out = bytearray(b"\x00" * 8 + bytes(out))[:size]
        elif n in (2, 3):               # 第 2、3 次：**同一个**陈旧值（关键）
            out = bytearray(b ^ 0x5A for b in out)
    return bytes(out)


def bump_reads() -> int:
    """回读次数 +1 并返回新值。跨进程，所以落文件（不能只放内存）。"""
    n = 0
    if os.path.exists(READ_COUNT):
        with open(READ_COUNT) as f:
            n = int((f.read().strip() or "0"))
    with open(READ_COUNT, "w") as f:
        f.write(str(n + 1))
    return n + 1


def load_state():
    """返回 (buf, wr, rd, follow)。

    follow 表示"正有另一个 RTT 读取端在持续排空缓冲"。它以**环境变量为准**，只在
    同一轮内被本工具的 w4 接管后失效（took_over）—— 反过来让持久化状态覆盖环境变量
    的话，连跑多个用例时前一个用例的 w4 会让后一个用例的 FAKE_RD_FOLLOW=1 静默失效。
    """
    env_follow = os.environ.get("FAKE_RD_FOLLOW") == "1"
    if os.path.exists(STATE):
        with open(STATE) as f:
            d = json.load(f)
        buf = bytearray.fromhex(d["buf"])
        if len(buf) != UP_SIZE:            # 上次跑用的尺寸不同，重建避免越界
            new = bytearray(UP_SIZE)
            new[:min(len(buf), UP_SIZE)] = buf[:UP_SIZE]
            buf = new
        # 字段一律用 get：旧格式状态文件缺字段时直接索引会 KeyError 让整个脚本崩掉，
        # 表现是"所有采集都报连接失败"，极难联想到是状态文件格式
        return (buf, d.get("wr", 0) % UP_SIZE, d.get("rd", 0) % UP_SIZE,
                env_follow and not d.get("took_over", False))
    return bytearray(UP_SIZE), 0, 0, env_follow


def save_state(buf, wr, rd, took_over):
    with open(STATE, "w") as f:
        json.dump({"buf": bytes(buf).hex(), "wr": wr, "rd": rd,
                   "took_over": took_over}, f)


def space(wr, rd):
    """SEGGER _GetAvailWriteSpace 的等价实现（容量是 SizeOfBuffer-1）。"""
    if rd <= wr:
        return UP_SIZE - 1 - wr + rd
    return rd - wr - 1


def advance(buf, wr, rd, text):
    """写入一行日志。整条放不下就整条丢弃、WrOff 原地不动 —— 这正是缺陷的表象。"""
    raw = text.encode("gbk")
    if len(raw) > space(wr, rd):
        return wr, False
    for byte in raw:
        buf[wr] = byte
        wr = (wr + 1) % UP_SIZE
    return wr, True


def write_cb(path, wr, rd, follow):
    cb = bytearray(64)
    cb[0:10] = b"SEGGER RTT"
    struct.pack_into("<II", cb, 16, 1, 3)          # NumUp=1 NumDown=3
    struct.pack_into("<IIII", cb, 28, UP_ADDR, UP_SIZE, wr, wr if follow else rd)
    struct.pack_into("<I", cb, 44, FLAGS)          # aUp[0].Flags
    with open(path, "wb") as f:
        f.write(cb)


def main(argv):
    if "-GoInteractive" in argv or "-hide" in argv:
        pass
    try:
        script_path = argv[argv.index("-CommanderScript") + 1]
    except (ValueError, IndexError):
        print("No script")
        return 1
    with open(script_path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    buf, wr, rd, follow = load_state()
    flash = load_flash()
    took_over = False                  # 本工具是否已 w4 接管读取端
    flash_touched = False              # 本轮是否动过 Flash（没动就别写文件，见文件尾）

    # 模拟「两次采样之间目标一直在打印」
    wr, _kept = advance(buf, wr, rd, "tick wr=%d\n" % wr)

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("savebin"):
            # Commander 两种写法都收：jlink_debug 用空格分隔，flash-jlink 用官方文档的
            # 逗号分隔。fake 认全，用例才不必为语法差异让步。
            parts = line.replace(",", " ").split()
            path, addr, size = parts[1], cnum(parts[2]), cnum(parts[3])
            if addr == CB_ADDR:
                write_cb(path, wr, rd, follow)
            elif addr == UP_ADDR:
                with open(path, "wb") as f:
                    f.write(bytes(buf[:size]))
            else:
                written = flash_read(flash, addr, size)
                if written is None:
                    # 可验算的小端数据：第 i 个 32bit 字 = addr + i
                    data = bytearray(size)
                    for i in range(size // 4):
                        struct.pack_into("<I", data, i * 4, addr + i)
                    written = bytes(data)
                with open(path, "wb") as f:
                    f.write(written)
        elif line.startswith("loadbin"):
            parts = line.replace(",", " ").split()[1:]
            with open(parts[0], "rb") as f:
                content = f.read()
            base = cnum(parts[1])
            flash = [r for r in flash if not (base <= r[0] < base + len(content))]
            flash.append([base, content.hex()])
            flash_touched = True
        elif line.startswith("w4"):
            parts = line.split()
            addr, value = int(parts[1], 0), int(parts[2], 0)
            if addr == RD_OFF and not IGNORE_W4:
                rd = value % UP_SIZE
                follow = False             # 本工具接管了读取端
                took_over = True
        elif line in ("r", "g", "qc", "exit", "connect", "go", "halt"):
            pass

    save_state(buf, wr, rd, took_over)
    if flash_touched:
        # 只在真用过 Flash 时才落盘：RTT 那批用例每次都走 savebin，若无条件写，
        # 工程里就会凭空多出一堆 *_state.json.flash —— 纯噪音。
        save_flash(flash)
    print("J-Link Commander V7.94b (compiled Jan  1 2024)")
    print(f"J-Link Commander will now exit on Error")   # -ExitOnError 1 的启动说明
    if SELF_VERIFY_FAIL:
        # 只影响**输出文本**，不动回读数据 —— 这正是"J-Link 自校验误报"的形状：
        # 它说失败，独立回读却是对的。
        # ⚠️ 下面三行必须**照真实的措辞**来：早先的 fake 只打一句 "Verification failed"，
        # 结果测不出"把启动横幅 'exit on Error' 当首条错误、还没回读就 return 1"这个真
        # 缺陷 —— 假得太干净，就只是在测我自己的想象。真实硬件上已复现过。
        print("J-Link: Flash download: Total: 6.399s (Prepare: 0.268s, Erase: 0.414s, "
              "Program: 3.746s, Verify: 0.556s)")
        print("****** Error: Verification failed @ address 0x08000000")
        print("Error while programming flash: Verify failed.")
    print("Script processing completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
