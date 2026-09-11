# -*- coding: utf-8 -*-
"""回归测试：flash-jlink 的独立回读校验（含 J-Link 自校验误报的判定）。

无硬件，用 fake_jlink.bat 模拟 JLink Commander 的 loadbin / savebin 通道。

这一组用例要锁住的核心区别**不是"烧没烧上"**，是"烧上了没有"这句话由谁来说：

  - J-Link 说校验失败但回读一致 → 判**误报**（F2）。这是实测踩到的真实场景，
    若代码只透传 J-Link 的结论，会把一次成功的烧录报成失败。
  - 回读真的对不上 → 判**真失败**并给出差异地址（F3）。反过来也要成立：
    不能因为"倾向于相信成功"就把真失败放过去。

F4/F5 专门盯 Intel HEX 的地址映射：段必须落在 0x08000000 而不是 0。
映射错位时 F5 会"通过"（两边都错到一处去），只有靠**手写**的期望值才能测出来 ——
所以用例里的 flash 内容一律硬编码，绝不用被测代码的解析器生成。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
FAKE = str(HERE / "fake_jlink.bat")
FLASHER = str(REPO / "tools" / "flash-jlink" / "scripts" / "jlink_flasher.py")

WORK = Path(tempfile.mkdtemp(prefix="ea_flash_test_"))
STATE = str(WORK / "state.json")
FLASH_STATE = STATE + ".flash"
READ_COUNT = FLASH_STATE + ".reads"

# 手写的期望内容（与下面的 HEX 一一对应，不经过被测解析器）
SEG0_ADDR, SEG0 = 0x08000000, bytes(range(32))
SEG1_ADDR, SEG1 = 0x08001000, b"\xAA" * 8


def ihex(addr, rtype, data):
    b = bytes([len(data), (addr >> 8) & 0xFF, addr & 0xFF, rtype]) + bytes(data)
    return ":" + (b + bytes([(-sum(b)) & 0xFF])).hex().upper()


HEX_TEXT = "\n".join([
    ihex(0, 0x04, [0x08, 0x00]),          # 扩展线性地址 → 基址 0x08000000
    ihex(SEG0_ADDR & 0xFFFF, 0x00, SEG0),
    ihex(SEG1_ADDR & 0xFFFF, 0x00, SEG1),
    ihex(0, 0x01, []),
]) + "\n"


def seed_flash(regions):
    """预置"板上已有的内容"（由用例手写，不经被测代码）。"""
    with open(FLASH_STATE, "w") as f:
        json.dump([[hex(a), b.hex()] for a, b in regions], f)
    # 回读次数也必须清零：F9 依赖"第 1 次回读错位"，上个用例残留的计数会让它
    # 一上来就是第 N 次，用例静默失去意义。
    if os.path.exists(READ_COUNT):
        os.remove(READ_COUNT)


def run(label, args, expect_rc, expect_out=(), expect_absent=(), env=None):
    full_env = dict(os.environ)
    full_env["FAKE_STATE"] = STATE
    full_env["PYTHONIOENCODING"] = "utf-8"
    full_env.update(env or {})
    for k in ("FAKE_FLASH_CORRUPT", "FAKE_SELF_VERIFY_FAIL",
              "FAKE_SHIFT_FIRST", "FAKE_UNSTABLE"):
        if k not in (env or {}):
            full_env.pop(k, None)

    # --settle 0：静置是**实机**才需要的（等 J-Link/目标的缓存收敛），fake 没有
    # 那个时序问题；留着它会让每个用例白等 10s，把测试从秒级拖到分钟级。
    proc = subprocess.run(
        [sys.executable, FLASHER, "--jlink", FAKE, "--settle", "0", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=full_env, timeout=180,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    problems = []
    if expect_rc is not None and proc.returncode != expect_rc:
        problems.append(f"退出码 {proc.returncode} != 期望 {expect_rc}")
    for needle in expect_out:
        if needle not in out:
            problems.append(f"输出缺少 {needle!r}")
    for needle in expect_absent:
        if needle in out:
            problems.append(f"输出不应出现 {needle!r}")

    icon = "PASS" if not problems else "FAIL"
    print(f"[{icon}] {label}")
    if problems:
        print("       " + "\n       ".join(problems))
        print("       --- 实际输出 ---")
        print("       " + "\n       ".join(out.strip().splitlines()[-25:]))
    return not problems


def make_bin(name, content, base="0x08000000"):
    p = WORK / name
    p.write_bytes(content)
    return str(p), ["--artifact", str(p), "--base-address", base]


def main() -> int:
    results = []
    payload = bytes(range(16))
    bin_path, bin_args = make_bin("fw.bin", payload)
    hex_path = WORK / "fw.hex"
    hex_path.write_text(HEX_TEXT, encoding="utf-8")

    # F1 烧录 + 回读一致（走 loadbin → 假 Flash → savebin 回读）。带 --json 一并锁住
    #    机读结论，脚本化编排（verify 四连）靠的就是它，不能只在人看的文本里对。
    seed_flash([])
    results.append(run("F1 bin 烧录后回读逐字节一致", [*bin_args, "--json"], 0,
                       ["烧录指令执行完毕", "逐字节一致", '"status": "success"',
                        '"verified": true']))

    # F2 J-Link 自校验报失败，但回读一致 → 误报
    seed_flash([])
    results.append(run("F2 J-Link 自校验误报 → 判误报而非失败",
                       bin_args, 0, ["误报", "烧录实际成功"],
                       ["真失败"],
                       env={"FAKE_SELF_VERIFY_FAIL": "1"}))

    # F3 回读真的对不上 → 真失败，并给出差异地址
    seed_flash([])
    results.append(run("F3 回读不符 → 判真失败并给差异地址",
                       bin_args, 1, ["真失败", "0x08000001"],
                       ["误报"],
                       env={"FAKE_FLASH_CORRUPT": "1"}))

    # F4 hex --verify-only 与板上内容一致（校验解析出的地址映射正确）
    seed_flash([(SEG0_ADDR, SEG0), (SEG1_ADDR, SEG1)])
    results.append(run("F4 hex 段地址映射正确 + 只读校验一致",
                       ["--artifact", str(hex_path), "--verify-only"], 0,
                       ["0x08000000", "0x08001000", "逐字节一致"]))

    # F5 板上第二段被改 → 必须报在第 8 个字节 0x08001007，而不是 0x00001007
    #    （地址映射若少了基址，差异地址会落错，这条才拦得住）
    seed_flash([(SEG0_ADDR, SEG0), (SEG1_ADDR, SEG1[:7] + b"\x00")])
    results.append(run("F5 hex 段内容不符 → 差异地址落在 0x08001007",
                       ["--artifact", str(hex_path), "--verify-only"], 1,
                       ["真失败", "0x08001007"]))

    # F6 BIN 缺基地址 → 明确拒绝，不猜地址
    results.append(run("F6 bin 缺 --base-address → 拒绝并说明",
                       ["--artifact", bin_path], 1, ["--base-address"]))

    # F7 --no-verify → 只烧不校验，不产生 MD5 结论
    seed_flash([])
    results.append(run("F7 --no-verify 不产生校验结论",
                       [*bin_args, "--no-verify"], 0, ["烧录指令执行完毕"],
                       ["逐字节一致", "md5"]))

    # F8 回读指令里的长度必须写成 0x 前缀。
    #    Commander 把裸数字当**十六进制**：实机 `..., 106104` 被当成 0x106104=1073412，
    #    读穿整片 Flash 拿回 512KB，于是一次**成功**的烧录被自己的回读判成"真失败"。
    #    这里直接盯生成出来的脚本文本 —— 一旦有人把它改回十进制，失败信息直指原因，
    #    而不是伪装成一次莫名其妙的"长度不符"。
    proc = subprocess.run(
        [sys.executable, FLASHER, "--jlink", FAKE, "--artifact", str(hex_path), "--dry-run"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "FAKE_STATE": STATE, "PYTHONIOENCODING": "utf-8"},
        timeout=120)
    rb_line = next((l for l in (proc.stdout or "").splitlines() if "savebin" in l), "")
    size_tok = rb_line.split(",")[-1].strip() if rb_line else ""
    ok = size_tok.startswith("0x") and int(size_tok, 16) == len(SEG0)
    print(f"[{'PASS' if ok else 'FAIL'}] F8 回读长度按十六进制书写（避免读穿 Flash）")
    if not ok:
        print(f"       期望末位参数 0x{len(SEG0):X}，实际 {size_tok!r}；整行: {rb_line!r}")
    results.append(ok)

    # F9 编程后**首次**回读整幅错位 +8 字节，第二次才正常（实机 J-Link 的真实行为）。
    #    工具必须靠"读两次求稳定"穿过它，最终仍给出**正确**结论，而不是把一次成功的
    #    烧录判成"真失败"。同时带 FAKE_SELF_VERIFY_FAIL：两个坑叠在一起时也该判**误报**。
    seed_flash([])
    results.append(run("F9 首次回读错位 → 重读至稳定后仍判误报",
                       bin_args, 0, ["误报", "烧录实际成功"],
                       ["真失败", "无法定论"],
                       env={"FAKE_SHIFT_FIRST": "1", "FAKE_SELF_VERIFY_FAIL": "1"}))

    # F10 每次回读都不一样 → 数据不稳定，必须判"无法定论"，**绝不能**报"真失败"
    #     （那是把可能成功的烧录误报成失败，比不给结论更坏），且退出码区别于成功/失败。
    seed_flash([])
    results.append(run("F10 回读始终不稳定 → 判无法定论而非真失败",
                       bin_args, 2, ["无法定论"],
                       ["真失败", "逐字节一致"],
                       env={"FAKE_UNSTABLE": "1"}))

    # F11 实机实录的完整收敛序列：错位 → 陈旧值 → **同一个陈旧值** → 正确。
    #     这段专治"连续两次相同就采信"：第 2、3 次一模一样却都是陈旧的，等它俩相等
    #     就下结论，等于把陈旧值当板上真相。必须熬到**连续三次**一致才可采信。
    seed_flash([])
    results.append(run("F11 陈旧平台期（两次相同但都旧）→ 仍判误报",
                       bin_args, 0, ["误报", "烧录实际成功"],
                       ["真失败", "无法定论"],
                       env={"FAKE_STALE_PLATEAU": "1", "FAKE_SELF_VERIFY_FAIL": "1"}))

    total, passed = len(results), sum(results)
    print("-" * 50)
    print(f"{passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
