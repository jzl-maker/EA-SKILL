#!/usr/bin/env python
"""EA-SKILL 测试生成器（/ea test）。

双产出：测试用例文档（.md）+ 可运行测试代码（.c / .py）。

用法：
  unit <文件|函数> [--cc gcc]        # 纯函数单测：生成 tests/unit/<name>.c + .md，
                                     # 用 --cc 编译运行，输出 PASS/FAIL
  board <功能>                       # 板级验收：套模板生成 tests/board/<case>.md
                                     # + 板载测试固件 .c 或 host 驱动脚本 .py

状态目录：.ea 优先、.em 兼容。产物写入 <STATE_DIR>/../tests/ 与工程 tests/ 并存。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import subprocess
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
elif sys.stdout:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
elif sys.stderr:
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 轻量自研断言宏 + host runner（零框架依赖）
UNIT_RUNNER = r"""
/* EA-SKILL 轻量 host 单测 runner（零依赖断言） */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int __ea_pass = 0, __ea_fail = 0;

#define ASSERT_TRUE(cond) do { \
    if (cond) { __ea_pass++; } \
    else { __ea_fail++; printf("  ✗ %s:%d ASSERT_TRUE(%s) 失败\n", __FILE__, __LINE__, #cond); } \
} while (0)

#define ASSERT_EQ(a, b) do { \
    long long _a = (long long)(a), _b = (long long)(b); \
    if (_a == _b) { __ea_pass++; } \
    else { __ea_fail++; printf("  ✗ %s:%d ASSERT_EQ(%s, %s) → %lld != %lld\n", __FILE__, __LINE__, #a, #b, _a, _b); } \
} while (0)

#define ASSERT_STR_EQ(a, b) do { \
    if (strcmp((a), (b)) == 0) { __ea_pass++; } \
    else { __ea_fail++; printf("  ✗ %s:%d ASSERT_STR_EQ(%s, %s) → \"%s\" != \"%s\"\n", __FILE__, __LINE__, #a, #b, (a), (b)); } \
} while (0)

static void __ea_summary(const char *suite) {
    printf("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");
    printf("[%s] PASS=%d FAIL=%d\n", suite, __ea_pass, __ea_fail);
    printf("%s\n", __ea_fail == 0 ? "TEST PASS" : "TEST FAIL");
    exit(__ea_fail == 0 ? 0 : 1);
}

/* ── 被测函数声明（AI 生成时替换） ── */
/* #include "target.h" */
/* int target_func(int a, int b); */

/* ── 硬件外设桩（AI 生成时按需替换） ── */
/* int mock_reg_read(void) { return 0; } */
"""

BOARD_CASE_TEMPLATE = r"""# 板级测试用例: {case}

## 基本信息
- 用例ID: {case_id}
- 功能: {feature}
- 测试类型: 板级工装验收
- 依赖: {deps}

## 前置条件
- [ ] 板卡上电，串口已连接
- [ ] {pre}

## 测试步骤
| 步骤 | 操作 | 预期 | 判定 |
|------|------|------|------|
{steps}

## 判定标准
- 全部步骤「预期」满足 → **PASS**
- 任一步骤失败 → 记录实际现象到 HVR，进入 /ea debug

## 测试代码
- 板载测试固件: `tests/board/{case}_test.c`（烧录后运行，经串口输出 `[TEST] PASS/FAIL`）
- 或 host 驱动脚本: `tests/board/{case}_driver.py`（用 serial/debug 工具自动执行 + 判定）
"""


def _state_dir(cwd: Path) -> Path:
    for d in (".ea", ".em"):
        p = cwd / d
        if p.is_dir():
            return p
    return cwd / ".ea"


def _to_slug(s: str) -> str:
    """功能描述 → **ASCII 安全**的文件名片段。

    这里曾经是"保留 CJK（Windows 文件名合法）"——**改掉了**。理由是它产出的 `.c`
    不是躺在磁盘上看的，是要加进 Keil 工程被编译烧录的，非 ASCII 路径在 Keil / CI /
    别人机器上都是已知的坑；文件名合法不等于工具链能吃。

    描述本身**不丢**：它照样写在用例文档标题和 `.c` 的文件头注释里，那里是给人看的。
    中文被丢弃时补一个**内容哈希**后缀 —— 否则 `om100_指纹…` 与 `om100_电机…`
    会双双塌成 `om100` 而互相覆盖（`om100_1a2b3c` / `om100_9f8e7d` 就分得开）。
    纯 ASCII 的描述完全不受影响（`led blink` → `led_blink`）。
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    slug = re.sub(r"_+", "_", slug)
    if not s.isascii():
        digest = hashlib.sha1(s.strip().encode("utf-8")).hexdigest()[:6]
        slug = f"{slug}_{digest}" if slug else f"case_{digest}"
    return slug[:40].strip("_") or "case"


def _comment_safe(s: str) -> str:
    """把描述压成能安全放进 C 块注释的一行。

    `*/` 会提前闭合注释、换行会把 /**/ 外的内容变成代码 —— 描述是用户随手写的，
    不能假设它干净。
    """
    return " ".join(s.replace("*/", "* /").split())


def _extract_functions(src: str) -> list[str]:
    """粗提取非 static 函数名（不含注释/宏）。"""
    body = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    body = re.sub(r"#.*", "", body)
    names = []
    for m in re.finditer(
        r"\b(?:int|void|char|unsigned\s+int|float|long|short|uint\d+_t)\s+(\w+)\s*\(", body
    ):
        names.append(m.group(1))
    return names


def cmd_unit(args: argparse.Namespace) -> int:
    target = args.target
    cwd = Path.cwd()
    state = _state_dir(cwd)

    src_path = Path(target)
    if src_path.suffix not in (".c", ".h"):
        print(f"❌ 目标必须是 .c/.h 文件或函数名: {target}")
        return 1
    if not src_path.is_file():
        print(f"❌ 文件不存在: {src_path}")
        return 1

    src = src_path.read_text(encoding="utf-8", errors="replace")
    funcs = _extract_functions(src)
    if not funcs:
        print("⚠️ 未提取到候选函数，可能全是 static/宏，建议改走 `ea test board`")
    print(f"📋 候选函数: {', '.join(funcs) or '(无)'}")

    base = src_path.stem
    out_dir = state / "tests" / "unit"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) 测试用例文档
    md = out_dir / f"{base}.md"
    lines = [
        f"# 单元测试用例: {base}",
        "",
        f"- 被测文件: {src_path}",
        f"- 候选函数: {', '.join(funcs) or '无'}",
        "- 运行: `gcc tests/unit/<name>.c 被测源文件 -o /tmp/ut && /tmp/ut`",
        "",
        "| 用例ID | 输入 | 预期 | 覆盖点 |",
        "|--------|------|------|--------|",
        "| T1 | 边界值 | ... | 正常路径 |",
        "| T2 | 非法输入 | ... | 异常路径 |",
        "",
        "> AI 生成：请核对输入/预期是否符合需求，再编译运行。",
    ]
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 2) 测试代码（.c）：runner + 桩 + 用例骨架
    c_file = out_dir / f"{base}.c"
    cases = "\n".join(
        f'    ASSERT_EQ({f}(0), 0); /* TODO: 核对输入/预期 */' for f in funcs[:5]
    ) if funcs else "    /* TODO: 无候选函数，需人工补充 */"
    c_src = f"""/* Unit test: {base} -- generated by /ea test unit, review */
{UNIT_RUNNER}

/* ── 用例入口 ── */
int main(void) {{
    printf("[suite] {base}\\n");
{cases}
    __ea_summary("{base}");
    return 0;
}}
"""
    c_file.write_text(c_src, encoding="utf-8")

    print(f"✅ 已生成: {md}")
    print(f"✅ 已生成: {c_file}")

    # 3) host 编译运行（可选）
    if args.cc:
        cmd = [args.cc, str(c_file), str(src_path), "-I", str(src_path.parent), "-o", str(c_file.with_suffix(".exe"))]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(f"❌ 编译失败（{args.cc}）:\n{r.stderr}")
            return 2
        run = subprocess.run([str(c_file.with_suffix(".exe"))], capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(run.stdout)
        if run.stdout.strip().endswith("TEST PASS"):
            return 0
        return 1
    print(f"ℹ️  未传 --cc，跳过编译运行。手动: gcc {out_dir}/<name>.c 被测源 -o /tmp/ut && /tmp/ut")
    return 0


def cmd_board(args: argparse.Namespace) -> int:
    feature = args.feature
    cwd = Path.cwd()
    state = _state_dir(cwd)

    case = _to_slug(feature)
    case_id = f"B-{case.upper()}"
    out_dir = state / "tests" / "board"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 从 context.md 读取依赖（芯片/外设），生成前置
    deps = "未指定（见 context.md）"
    ctx = state / "context.md"
    if ctx.is_file():
        t = ctx.read_text(encoding="utf-8", errors="replace")
        for key in ("芯片", "主控", "MCU"):
            for line in t.splitlines():
                if key in line and ":" in line:
                    deps = line.strip()
                    break
            if deps != "未指定（见 context.md）":
                break

    steps = (
        "| 1 | 上电并复位 | 启动日志出现，[TEST] 帧开始 | 观察串口 |\n"
        "| 2 | 执行功能操作 | 输出符合预期 | 观察串口 |\n"
        "| 3 | 检查异常/复位 | 无看门狗复位、无 hardfault | 观察 RTT/串口 |"
    )
    md = out_dir / f"{case}.md"
    content = BOARD_CASE_TEMPLATE.format(
        case=case, case_id=case_id, feature=feature, deps=deps, pre="板卡连接正常", steps=steps
    )
    md.write_text(content, encoding="utf-8")

    # 板载测试固件（.c）：协议输出 [TEST] PASS/FAIL
    #
    # 文件名是 ASCII 短标识（见 _to_slug），**原始描述只在这里出现** —— 不加这行的话，
    # Keil 里打开 om100_c5fbe6_test.c 谁也不知道是哪个用例。
    # 编码用 GBK：这文件是要加进 Keil 工程的，Keil 默认按 GB2312 读源码，写成 UTF-8
    # 中文在 IDE 里是乱码（与 SKILL.md 红线 5 同一条）。改这里时别顺手换成 utf-8。
    fw = out_dir / f"{case}_test.c"
    fw_text = (
        # {{feature}} 双括号：留给下面 .replace 填，绕开 f-string 直接取值（值里可能带
        # 换行/`*/`，直接塞进 C 注释会把注释提前闭合）
        f"""/* Board test firmware: {case} -- generated by /ea test board
 * 用例: {{feature}}
 */
#include <stdio.h>
/* include target module as needed */
/* #include "app.h" */

/* printf redirected by project (note GBK source encoding) */
int test_{case}_entry(void)
{{
    printf("[TEST] {case_id} start\\r\\n");
    /* TODO: call logic under test and report result */
    /* if (run_feature() == 0) {{ printf("[TEST] {case_id} PASS\\r\\n"); }} */
    /* else {{ printf("[TEST] {case_id} FAIL\\r\\n"); }} */
    printf("[TEST] {case_id} FAIL (not implemented)\\r\\n");
    return 1;
}}
"""
    ).replace("{feature}", _comment_safe(feature))
    # errors="replace"：GBK 收不下的字符（emoji 等）落成 '?'，比整个文件写不出去强
    fw.write_text(fw_text, encoding="gbk", errors="replace")
    print(f"✅ 已生成用例文档: {md}")
    print(f"✅ 已生成板载测试固件: {fw}")
    print(f"ℹ️  驱动脚本 {case}_driver.py（可选）由 AI 按串口/调试接口自动生成")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_unit = sub.add_parser("unit", help="纯函数 host 单测")
    p_unit.add_argument("target", help="目标 .c/.h 文件路径")
    p_unit.add_argument("--cc", default=None, help="C 编译器路径（如 gcc），传则编译运行")
    p_board = sub.add_parser("board", help="板级工装验收用例")
    p_board.add_argument("feature", help="功能描述")
    args = parser.parse_args(argv)

    if args.cmd == "unit":
        return cmd_unit(args)
    return cmd_board(args)


if __name__ == "__main__":
    raise SystemExit(main())
