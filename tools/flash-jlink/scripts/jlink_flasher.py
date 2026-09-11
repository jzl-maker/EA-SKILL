#!/usr/bin/env python
"""/ea flash --backend jlink-native：用 J-Link 原生通道烧录 + 独立回读校验。

**为什么需要这个工具**：J-Link 接了探针时，OpenOCD 走 libusb 拿不到设备句柄
（`LIBUSB_ERROR_NOT_SUPPORTED`，SEGGER 驱动占着），`interface/jlink.cfg` 也救不回来
（与 transport 无关）。此时只剩 JLink 官方通道可用。

**它真正解决的问题不是"能烧"，是"能判断烧没烧上"**：JLink 自带的校验结论偶发误报
（实测出现过 `Verification failed` 但目标实际完好）。只信它自己的校验逻辑，等于让
被怀疑的一方给自己作证。所以这里：

  1. 烧录（`loadfile` / `loadbin`）
  2. **独立回读**：`savebin` 把 Flash 内容读回来（走 AHB-AP，不 halt）
  3. 与**从产物文件自己解析出的期望字节**逐段比对（MD5 + 首个差异字节）

第 3 步的期望值来自本脚本对 .hex/.axf/.elf 的解析，与 J-Link 的校验实现完全无关。
两者一致 ⇒ 即使 JLink 打印了失败也是**误报**；不一致 ⇒ **真失败**，并给出差异偏移。

支持产物：`.hex`（Intel HEX）、`.bin`（需 `--base-address`）、`.elf`/`.axf`（PT_LOAD 段）。
零第三方依赖：只调用 J-Link 可执行文件。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_SCRIPT_DIR = Path(__file__).resolve().parent
_SKILLS_DIR = _SCRIPT_DIR.parent.parent
for _candidate in [_SKILLS_DIR / "shared", _SKILLS_DIR.parent / "shared"]:
    if (_candidate / "tool_config.py").exists():
        sys.path.insert(0, str(_candidate))
        break

from jlink_commander import (  # noqa: E402
    DEFAULT_DEVICE, DEFAULT_INTERFACE, DEFAULT_SPEED,
    build_commander_script, find_jlink, run_commander, to_script_path,
)

# J-Link 自报的校验失败字样。命中不代表真失败 —— 见模块 docstring。
SELF_VERIFY_FAIL_MARKERS = ("verification failed", "verify failed", "verification error")
# Commander 连不上目标时的典型输出
CONNECT_FAIL_MARKERS = ("cannot connect", "could not connect", "no device found",
                        "failed to connect", "error while connecting")
# 不是错误的"Error"字样：`-ExitOnError 1` 会让 Commander 一启动就打印这行说明，
# 它出现在**输出最前面**。按"含 error 的行"去取首条错误会取到它 —— 实测因此把一次
# 正常的（只是自校验报了失败）烧录误判成"烧录未完成"并提前 return，**连回读都没跑**，
# 恰好毁掉本工具存在的意义。
ERROR_LINE_IGNORE = ("will now exit on error",)

FLASH_TIMEOUT_S = 300
VERIFY_TIMEOUT_S = 120
# 回读需要**连续 N 次完全一致**才采信。为什么不是 2：实测序列是
#   错位镜像 → 陈旧值A → 陈旧值A（两次一模一样！）→ 正确 → 正确
# 也就是说"连续两次相同"会稳稳地锁死在那对**同样陈旧**的值上，把它当成板上真相。
# 3 次才勉强越过实测看到的陈旧平台；上限 8 次是给慢一点的情况留余量。
READBACK_STABLE_N = 3
READBACK_MAX_ATTEMPTS = 8
# 编程后静置多久再回读。实测：编完立刻读是错位的，间隔读 4 次（约 4/7/10/13s）才在
# 第 4 次转为正确；而"编完睡 8s 再读**一次**"就已经正确 —— 说明是**时间**在收敛
# （J-Link/目标侧的缓存刷新），不是"多读几次"。故先静置再读，比硬读八次又快又稳。
POST_PROGRAM_SETTLE_S = 10.0
MD5 = hashlib.md5


# ---------------------------------------------------------------------------
# 产物解析 → 期望字节
# ---------------------------------------------------------------------------

def parse_intel_hex(text: str) -> list[tuple[int, bytes]]:
    """解析 Intel HEX → `[(起始地址, 数据)]`（未合并）。

    记录格式 `:LLAAAATT[DD...]CC`：TT=00 数据 / 01 结束 / 02 扩展段(<<4) / 04 扩展线性(<<16)。
    03/05（入口点）忽略 —— 它们是执行地址，不是加载内容。
    """
    base = 0
    out: list[tuple[int, bytes]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise ValueError(f"第 {lineno} 行不是 Intel HEX 记录（应以 ':' 开头）: {line[:20]}")
        try:
            rec = bytes.fromhex(line[1:])
        except ValueError as exc:
            raise ValueError(f"第 {lineno} 行十六进制不合法: {line[:20]}") from exc
        if len(rec) < 5:
            raise ValueError(f"第 {lineno} 行记录过短")
        length, addr, rtype = rec[0], (rec[1] << 8) | rec[2], rec[3]
        data = rec[4:4 + length]
        if len(data) != length:
            raise ValueError(f"第 {lineno} 行声明 {length} 字节但只有 {len(data)} 字节")
        if rtype == 0x00:
            out.append((base + addr, data))
        elif rtype == 0x01:
            break
        elif rtype == 0x02:
            if len(data) < 2:
                raise ValueError(f"第 {lineno} 行扩展段地址记录缺数据")
            base = ((data[0] << 8) | data[1]) << 4
        elif rtype == 0x04:
            if len(data) < 2:
                raise ValueError(f"第 {lineno} 行扩展线性地址记录缺数据")
            base = ((data[0] << 8) | data[1]) << 16
        # 03/05 忽略
    if not out:
        raise ValueError("Intel HEX 中没有任何数据记录（TT=00）—— 文件可能是空的")
    return out


def parse_elf_loadable(path: Path) -> list[tuple[int, bytes]]:
    """解析 ELF/axf 的 PT_LOAD 段 → `[(物理地址, 内容)]`。

    只取 `p_filesz > 0` 的段：`p_filesz < p_memsz` 的尾部是 .bss，Flash 里没有对应字节，
    回读时读到的是 0xFF 或旧值，拿它去比对必然假失败。
    """
    data = path.read_bytes()
    if data[:4] != b"\x7fELF":
        raise ValueError("不是 ELF 文件（magic 不匹配）")
    if data[5] != 1:
        raise ValueError("不支持的字节序（仅支持小端 ELF）")
    is64 = data[4] == 2
    if is64:
        e_phoff = struct.unpack_from("<Q", data, 32)[0]
        e_phentsize, e_phnum = struct.unpack_from("<HH", data, 54)
        off_paddr, off_filesz = 24, 32       # p_offset 在 +8
    else:
        e_phoff = struct.unpack_from("<I", data, 28)[0]
        e_phentsize, e_phnum = struct.unpack_from("<HH", data, 42)
        off_paddr, off_filesz = 12, 16       # p_offset 在 +4
    if not e_phoff or not e_phnum:
        raise ValueError("ELF 没有程序头表，无法定位加载段")

    out: list[tuple[int, bytes]] = []
    for i in range(e_phnum):
        base = e_phoff + i * e_phentsize
        if base + e_phentsize > len(data):
            break
        p_type = struct.unpack_from("<I", data, base)[0]
        if p_type != 1:                       # PT_LOAD
            continue
        p_offset = struct.unpack_from("<Q" if is64 else "<I", data, base + (8 if is64 else 4))[0]
        p_paddr = struct.unpack_from("<Q" if is64 else "<I", data, base + off_paddr)[0]
        p_filesz = struct.unpack_from("<Q" if is64 else "<I", data, base + off_filesz)[0]
        if p_filesz == 0:
            continue
        # 越界即文件被截断（或 p_offset 不可信）。不拦住的话切片会静默返回空/短数据，
        # 回读比对就会拿一份**错误的期望值**去判"真失败" —— 比读不出来更坏。
        if p_offset + p_filesz > len(data):
            raise ValueError(
                f"ELF 被截断：第 {i} 个加载段声明 offset=0x{p_offset:X} len={p_filesz}"
                f"，超出文件大小 {len(data)} 字节")
        out.append((p_paddr, data[p_offset:p_offset + p_filesz]))
    if not out:
        raise ValueError("ELF 中没有可加载段（p_filesz > 0 的 PT_LOAD）")
    return out


def merge_ranges(items: list[tuple[int, bytes]]) -> list[tuple[int, bytes]]:
    """把 (地址, 数据) 合并成不重叠、连续段。

    Intel HEX 的数据记录是 16/32 字节一条，逐条回读要发起上百次 savebin；
    合并后通常只剩 1~2 段。重叠部分按后出现者覆盖（HEX 中后者合法地覆盖前者）。
    """
    if not items:
        return []
    ordered = sorted(items, key=lambda x: x[0])
    merged: list[list[Any]] = [[ordered[0][0], bytearray(ordered[0][1])]]
    for addr, chunk in ordered[1:]:
        last_addr, last_buf = merged[-1]
        last_end = last_addr + len(last_buf)
        if addr > last_end:
            merged.append([addr, bytearray(chunk)])
            continue
        # 重叠或相接
        overlap = last_end - addr
        if overlap < 0:
            overlap = 0
        if overlap >= len(chunk):
            continue                          # 完全被覆盖
        # 逐字节覆盖（overlap 通常为 0，走 fast path）
        if overlap:
            last_buf[addr - last_addr:last_end] = chunk[:overlap]
        last_buf.extend(chunk[overlap:])
    return [(a, bytes(b)) for a, b in merged]


def load_expected(artifact: Path, base_address: int | None) -> tuple[list[tuple[int, bytes]], str]:
    """读产物 → `(期望段, 类型)`。类型用于决定 JLink 的烧录命令。"""
    kind = {".hex": "hex", ".bin": "bin", ".elf": "elf", ".axf": "elf"}.get(artifact.suffix.lower())
    if kind is None:
        raise ValueError(f"不支持的产物类型: {artifact.suffix}（支持 .hex/.bin/.elf/.axf）")
    if kind == "hex":
        return merge_ranges(parse_intel_hex(artifact.read_text(encoding="utf-8", errors="replace"))), kind
    if kind == "bin":
        if base_address is None:
            raise ValueError("BIN 文件必须提供 --base-address（烧录基地址），否则无从得知烧到哪")
        return [(base_address, artifact.read_bytes())], kind
    ranges = merge_ranges(parse_elf_loadable(artifact))
    if base_address is not None:
        ranges = [(base_address + a - ranges[0][0], b) for a, b in ranges]
    return ranges, kind


# ---------------------------------------------------------------------------
# 烧录 / 回读
# ---------------------------------------------------------------------------

def flash_body(kind: str, artifact: Path, base_address: int | None, do_reset: bool) -> list[str]:
    """JLink Commander 的烧录指令序列。

    `loadfile` 自己认 .hex/.elf 的地址；.bin 没有地址信息，必须走 `loadbin <file>, <addr>`。
    `r` + `g` 放在最后：复位后立刻放行，避免目标停在复位向量处被后续 savebin 读到空 Flash。
    """
    body = [f"loadfile {to_script_path(artifact)}"]
    if kind == "bin":
        body = [f"loadbin {to_script_path(artifact)}, 0x{base_address:08X}"]
    if do_reset:
        body += ["r", "g"]
    return body


def readback_body(ranges: list[tuple[int, bytes]], out_dir: Path,
                  display_dir: str | None = None) -> tuple[list[str], list[Path]]:
    """回读指令序列 + 对应的落地文件路径。一次连接读完全部段。

    `display_dir` 只改**打印出来的**路径（dry-run 里写 `<tmp>/readback_0.bin` 比写出
    一个并不存在的绝对路径诚实），实际落地路径始终是 `out_dir`。
    """
    body: list[str] = []
    files: list[Path] = []
    for i, (addr, buf) in enumerate(ranges):
        dst = out_dir / f"readback_{i}.bin"
        shown = f"{display_dir}/readback_{i}.bin" if display_dir else to_script_path(dst)
        # ⚠️ 长度**必须带 0x**。Commander 的裸数字按**十六进制**解析：实测 `106104`
        # 被当成 0x106104=1073412，读穿整个 Flash 拿回 512KB，比对报"长度不符"，
        # 于是一次**成功**的烧录被自己的回读判成"真失败"。地址自带 0x 所以没露馅，
        # 长度是唯一一处裸数字。（jlink_debug 早先就写了 0x，故那边无此问题。）
        body.append(f"savebin {shown}, 0x{addr:08X}, 0x{len(buf):X}")
        files.append(dst)
    return body, files


def parse_commander_failure(out: str) -> str | None:
    """从 Commander 输出里挑出**连接/配置类**失败原因，成功返回 None。

    ⚠️ **只认"命令没能执行"这一类**。J-Link 自报的校验失败（`Verification failed`）
    不在此列 —— 那正是要靠独立回读去裁决的争议结论，在这里当成失败就等于让它自证。
    见 `do_flash` 里对 `self_verify_failed` 的处理。
    """
    lowered = out.lower()
    for marker in CONNECT_FAIL_MARKERS:
        if marker in lowered:
            return f"无法连接目标（输出含 '{marker}'）"
    for line in out.splitlines():
        low = line.strip().lower()
        if any(b in low for b in ERROR_LINE_IGNORE):
            continue
        # 自校验失败同样不是"命令没执行"：烧录阶段它是待裁决的争议结论（见 do_flash），
        # 回读阶段它根本不该出现（但同一个脚本里既 loadbin 又 savebin 时会混在一起，
        # 那时它只说明**烧录那半段**有争议，回读出来的字节照样是有效证据）。
        # 两处都放行 —— 否则回读会在拿到数据之前就中止，等于让 J-Link 一票否决。
        if any(m in low for m in SELF_VERIFY_FAIL_MARKERS):
            continue
        if low.startswith("*** error") or "error:" in low:
            return line.strip()
    return None


def compare(expected: list[tuple[int, bytes]], actual_files: list[Path]
            ) -> tuple[bool, list[dict[str, Any]], list[str]]:
    """逐段比对 → `(是否全部一致, 每段结论, 差异描述)`。"""
    verdicts: list[dict[str, Any]] = []
    diffs: list[str] = []
    all_ok = True
    for (addr, buf), path in zip(expected, actual_files):
        entry: dict[str, Any] = {
            "address": f"0x{addr:08X}",
            "size": len(buf),
            "expected_md5": MD5(buf).hexdigest(),
        }
        if not path.exists():
            entry |= {"status": "inconclusive", "reason": "回读文件未生成（savebin 未执行成功）"}
            verdicts.append(entry)
            diffs.append(f"{entry['address']} 段未取回，无法判定")
            all_ok = False
            continue
        got = path.read_bytes()
        entry["actual_md5"] = MD5(got).hexdigest()
        if got == buf:
            entry["status"] = "match"
            verdicts.append(entry)
            continue
        entry["status"] = "mismatch"
        all_ok = False
        if len(got) != len(buf):
            entry["reason"] = f"长度不符：期望 {len(buf)}，实际 {len(got)}"
            diffs.append(f"{entry['address']}: {entry['reason']}")
        else:
            idx = next(i for i in range(len(buf)) if buf[i] != got[i])
            n_diff = sum(1 for i in range(len(buf)) if buf[i] != got[i])
            entry["first_diff_offset"] = idx
            entry["first_diff_address"] = f"0x{addr + idx:08X}"
            entry["expected_byte"] = f"0x{buf[idx]:02X}"
            entry["actual_byte"] = f"0x{got[idx]:02X}"
            entry["diff_bytes"] = n_diff
            diffs.append(f"{entry['first_diff_address']} 期望 {entry['expected_byte']} "
                         f"实际 {entry['actual_byte']}（共 {n_diff} 字节不符）")
        verdicts.append(entry)
    return all_ok, verdicts, diffs


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def do_flash(args) -> int:
    verify = not args.no_verify
    artifact = Path(args.artifact).resolve()
    if not artifact.is_file():
        print(f"❌ 产物不存在: {artifact}")
        return 1

    base_address = int(args.base_address, 16) if args.base_address else None
    try:
        expected, kind = load_expected(artifact, base_address)
    except (ValueError, OSError) as exc:
        print(f"❌ 解析产物失败: {exc}")
        return 1

    total = sum(len(b) for _a, b in expected)
    print(f"📦 产物: {artifact.name} [{kind.upper()}, {total} 字节]")
    for addr, buf in expected:
        print(f"   {kind.upper()} 段 0x{addr:08X}  长度 {len(buf)}")

    jlink_exe, _ = find_jlink(args.jlink)

    # dry-run 先于 JLink 存在性检查：只为看指令而要求装好 JLink，等于干跑不了。
    if args.dry_run:
        print(f"🔌 探针: {jlink_exe or '<未找到 JLink.exe>'}  "
              f"device={args.device} if={args.interface} speed={args.speed}")
        print("\n🔍 dry-run：烧录指令")
        for line in build_commander_script(args.device, args.interface, args.speed,
                                           flash_body(kind, artifact, base_address,
                                                      not args.no_reset)).splitlines():
            print(f"   {line}")
        if not verify:
            print("\n   （回读校验已禁用）")
        elif args.verify_only:
            print("\n   （--verify-only：跳过上面这段）")
        print("\n🔍 dry-run：独立回读指令")
        for line in readback_body(expected, Path("."), display_dir="<tmp>")[0]:
            print(f"   {line}")
        return 0

    if not jlink_exe:
        print("❌ 未找到 JLink.exe。用 --jlink 指定，或 /ea setup 注册路径。")
        return 1
    print(f"🔌 探针: {jlink_exe}  device={args.device} if={args.interface} speed={args.speed}")

    # ---- 1) 烧录 ----
    if not args.verify_only:
        script = build_commander_script(args.device, args.interface, args.speed,
                                        flash_body(kind, artifact, base_address, not args.no_reset))
        try:
            res = run_commander(jlink_exe, script, timeout=args.timeout,
                                device=args.device, interface=args.interface, speed=args.speed)
        except Exception as exc:                  # noqa: BLE001 - 超时/启动失败都要给出可读结论
            print(f"❌ 烧录调用失败: {exc}")
            return 1
        out = res.stdout or ""
        self_verify_failed = any(m in out.lower() for m in SELF_VERIFY_FAIL_MARKERS)
        fail = parse_commander_failure(out)
        if self_verify_failed:
            # 关键分岔：自校验失败**不能**在这里中止。中止等于让 J-Link 给自己作证，
            # 而本工具的全部价值就在于由独立回读来裁决这一点。继续往下走。
            print("✅ 烧录指令已执行，但 ⚠️ JLink 自带校验报了失败")
            print("   → 不做结论，转独立回读比对（下面这一步才是裁判）")
            fail = None
        if fail:
            print(f"❌ 烧录未完成: {fail}")
            print_evidence(out)
            return 1
        if not self_verify_failed:
            print("✅ 烧录指令执行完毕")
    else:
        self_verify_failed = False
        print("ℹ️ --verify-only：跳过烧录，只做回读比对")

    if not verify:
        result = {"status": "success", "flashed": not args.verify_only, "verified": False}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # ---- 2) 独立回读 ----
    # ⚠️ 什么时候读、读几次才算数 —— 这块是实机踩出来的，别想当然。
    # 现象（N32G4FR + J-Link V7.94b）：编程后**立刻**回读，拿到的整幅镜像是错位的，
    # 且随后会连着给出**两次一模一样但同样陈旧**的值，之后才收敛到正确内容。
    # 不设防的话，**每一次成功的烧录都会被判成"真失败"** —— 与"把 J-Link 自校验的
    # 误报当成失败"恰好是同一个错误的两面。
    # 对策两条：① 编程后先静置（时间是收敛变量，实测睡 8s 后单次读即正确）；
    # ② 必须连续 N 次一致才采信（"两次相同"会被陈旧的平台期骗过去，见 READBACK_STABLE_N）。
    # 始终不稳则判"无法定论"，绝不硬给"真失败"。
    if not args.verify_only and args.settle > 0:
        print(f"⏳ 编程后静置 {args.settle:g}s 再回读"
              f"（刚编程完立刻读会拿到错位/陈旧镜像）")
        time.sleep(args.settle)
    need_pairs = 1 if args.verify_only else READBACK_STABLE_N - 1
    tmp_root = Path(tempfile.mkdtemp(prefix="ea_jlink_rb_"))
    try:
        files: list[Path] = []
        prev: list[bytes] | None = None
        streak = 0
        for attempt in range(1, READBACK_MAX_ATTEMPTS + 1):
            attempt_dir = tmp_root / f"a{attempt}"
            attempt_dir.mkdir()
            body, files = readback_body(expected, attempt_dir)
            script = build_commander_script(args.device, args.interface, args.speed, body)
            try:
                res = run_commander(jlink_exe, script, timeout=args.timeout,
                                    device=args.device, interface=args.interface,
                                    speed=args.speed)
            except Exception as exc:              # noqa: BLE001
                print(f"❌ 回读调用失败: {exc}")
                return 1
            fail = parse_commander_failure(res.stdout or "")
            if fail:
                print(f"⚠️ 回读未完成: {fail}")
                print_evidence(res.stdout or "")
                return 1

            cur = [f.read_bytes() if f.exists() else b"" for f in files]
            if prev is not None and cur == prev:
                streak += 1
                if streak >= need_pairs:
                    if attempt > 1:
                        print(f"ℹ️ 连续 {streak + 1} 次回读一致，数据已稳定")
                    break
            else:
                if prev is not None:
                    print(f"⚠️ 第 {attempt} 次回读与前次**不一致**"
                          f"（疑似刚编程完的错位/陈旧镜像），重读…")
                streak = 0
            prev = cur
        else:
            # 读满次数仍未稳定 —— 这是"测不准"，不是"烧失败"。绝不给出"真失败"的结论：
            # 那会把一次可能成功的烧录误报成失败，比不给结论更坏。
            print(f"❌ **无法定论**：连续 {READBACK_MAX_ATTEMPTS} 次回读彼此不一致，"
                  f"数据不稳定，无法作为裁决依据。")
            print("   排查 → ① 目标是否在持续改写 Flash  ② 调试口速率过高（试 --speed 1000）"
                  "  ③ 供电/接线")
            if args.json:
                print(json.dumps({"status": "unstable", "flashed": not args.verify_only,
                                  "verified": None, "jlink_self_verify_failed": self_verify_failed,
                                  "false_positive": False}, ensure_ascii=False, indent=2))
            return 2

        # ---- 3) 比对 ----
        all_ok, verdicts, diffs = compare(expected, files)
        for v in verdicts:
            icon = {"match": "✅", "mismatch": "❌"}.get(v["status"], "⚠️")
            line = f"   {icon} {v['address']}  {v['size']} 字节  md5 {v['expected_md5']}"
            if v["status"] == "mismatch":
                line += f" ≠ {v.get('actual_md5')}"
            print(line)
            if v.get("reason"):
                print(f"       {v['reason']}")

        if all_ok and self_verify_failed:
            print("\n📊 结论: ⚠️ J-Link 报告校验失败，但独立回读与产物**完全一致**"
                  "\n   → 判定为 J-Link 侧**误报**，烧录实际成功。以本结论为准（回读证据与 J-Link 校验实现无关）。")
        elif all_ok:
            print("\n📊 结论: ✅ 烧录成功，且独立回读逐字节一致")
        else:
            print("\n📊 结论: ❌ **真失败** —— 回读内容与产物不符：")
            for d in diffs[:10]:
                print(f"   {d}")
            print("   排查 → ① 产物是否是最新编译的（--rebuild 后重试）"
                  "  ② 目标 Flash 是否被写保护/扇区擦除失败  ③ 供电与接线")

        if args.json:
            print(json.dumps({
                "status": "success" if all_ok else "mismatch",
                "flashed": not args.verify_only,
                "verified": all_ok,
                "jlink_self_verify_failed": self_verify_failed,
                "false_positive": bool(all_ok and self_verify_failed),
                "ranges": verdicts,
            }, ensure_ascii=False, indent=2))
        return 0 if all_ok else 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def print_evidence(out: str, n: int = 15) -> None:
    lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
    if not lines:
        return
    print("\n📝 原始输出（末 %d 行）:" % min(n, len(lines)))
    for line in lines[-n:]:
        print(f"   {line}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="J-Link 原生烧录 + 独立回读校验（/ea flash --backend jlink-native）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  py jlink_flasher.py --artifact build/app.hex
  py jlink_flasher.py --artifact build/app.elf --no-reset
  py jlink_flasher.py --artifact build/fw.bin --base-address 0x08000000
  py jlink_flasher.py --artifact build/app.hex --verify-only   # 只回读比对，不烧
  py jlink_flasher.py --artifact build/app.hex --dry-run
        """,
    )
    parser.add_argument("--artifact", help="固件产物路径（.hex/.bin/.elf/.axf）")
    parser.add_argument("--base-address", help="BIN 烧录基地址（十六进制，如 0x08000000）")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help=f"芯片型号（默认 {DEFAULT_DEVICE}）")
    parser.add_argument("--if", dest="interface", default=DEFAULT_INTERFACE,
                        help=f"调试接口（默认 {DEFAULT_INTERFACE}）")
    parser.add_argument("--speed", type=int, default=DEFAULT_SPEED, help=f"速率 kHz（默认 {DEFAULT_SPEED}）")
    parser.add_argument("--jlink", help="JLink.exe 路径（默认自动查找）")
    parser.add_argument("--timeout", type=int, default=FLASH_TIMEOUT_S, help="单次调用超时秒数")
    parser.add_argument("--verify-only", action="store_true", help="跳过烧录，只回读比对")
    parser.add_argument("--no-verify", action="store_true", help="跳过回读校验")
    parser.add_argument("--no-reset", action="store_true", help="烧录后不复位放行")
    parser.add_argument("--settle", type=float, default=POST_PROGRAM_SETTLE_S,
                        help=f"编程后静置多少秒再回读（默认 {POST_PROGRAM_SETTLE_S:g}；"
                             f"刚编完立刻读会拿到错位/陈旧镜像，0=不等）")
    parser.add_argument("--json", action="store_true", help="额外输出 JSON 结论")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的指令")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.artifact:
        print("❌ 请提供 --artifact（固件产物路径）。")
        return 1
    return do_flash(args)


if __name__ == "__main__":
    sys.exit(main())
