# -*- coding: utf-8 -*-
"""Fake JLink.exe：模拟 -CommanderScript 的 savebin / w4，无硬件验证 RTT 采集链路。

关键是要**忠实模拟目标侧的环形缓冲语义**（单消费者 + 写满即丢），否则测不出
"读取端不推进 RdOff" 这个缺陷：

  - 每次脚本执行前写一行新日志（模拟"两次采样之间目标一直在打印"）
  - 空间不够就**整条丢弃**、WrOff 原地不动（NO_BLOCK_SKIP）—— 即 WrOff 冻住的现象
  - 只有 w4 写回 RdOff 之后空间才释放，后续日志才续得上

环境变量：
  FAKE_UP_SIZE    环形缓冲大小（默认 0x800）
  FAKE_RTT_FLAGS  aUp[0].Flags（0=NO_BLOCK_SKIP 2=BLOCK_IF_FIFO_FULL）
  FAKE_RD_FOLLOW  1=模拟另一 RTT 读取端始终把 rd 追到 wr（直到我们 w4 接管）
  FAKE_IGNORE_W4  1=模拟写回不生效（目标拒绝写 / RTT 被重新初始化）
  FAKE_STATE      状态文件路径（默认同目录 state.json）；测试应各自指定避免串扰
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
    took_over = False                  # 本工具是否已 w4 接管读取端

    # 模拟「两次采样之间目标一直在打印」
    wr, _kept = advance(buf, wr, rd, "tick wr=%d\n" % wr)

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("savebin"):
            parts = line.split()
            path, addr, size = parts[1], int(parts[2], 0), int(parts[3], 0)
            if addr == CB_ADDR:
                write_cb(path, wr, rd, follow)
            elif addr == UP_ADDR:
                with open(path, "wb") as f:
                    f.write(bytes(buf[:size]))
            else:
                # 可验算的小端数据：第 i 个 32bit 字 = addr + i
                data = bytearray(size)
                for i in range(size // 4):
                    struct.pack_into("<I", data, i * 4, addr + i)
                with open(path, "wb") as f:
                    f.write(bytes(data))
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
    print("J-Link Commander V7.94b (compiled Jan  1 2024)")
    print("Script processing completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
