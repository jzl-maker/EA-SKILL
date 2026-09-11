# -*- coding: utf-8 -*-
"""回归测试：D 批（体验）—— S-15 / S-16 / S-21 / S-23 / S-25。

这几条都不是"算错了"，是"**没说清楚**"：空 diff 看着像没改动、同一个工具既
"未找到"又"已注册"、无参运行一声不吭、描述直接当文件名。所以用例盯的是
**输出里有没有那句话**，以及**退出码能不能让调用方分辨**。

不含 S-10（`/ea stat -v` 的步骤号校验是命令文档里的执行步骤，没有可测的代码路径）。

全程不碰真实环境：
  - `tool_config` 的全局配置路径取自 %APPDATA%，所以子进程里把它指到临时目录
  - `detect_tools` 会**写**注册表结果进全局配置，必须隔离，否则污染本机
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 用例名与说明都是中文 + 符号，控制台默认 GBK 会在 print 时直接抛 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
SHARED = REPO / "tools" / "shared"
TEST_GEN = REPO / "tools" / "test-runner" / "scripts" / "test_gen.py"
WORK = Path(tempfile.mkdtemp(prefix="ea_exp_"))


def run(args, cwd=None, env=None, timeout=120):
    full = dict(os.environ)
    full["PYTHONIOENCODING"] = "utf-8"
    # 隔离全局配置：不设的话 tool_config 会去读/写真实的 %APPDATA%/ea_skill/config.json
    full["APPDATA"] = str(WORK / "appdata")
    full["XDG_CONFIG_HOME"] = str(WORK / "xdg")
    full.update(env or {})
    proc = subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=cwd, env=full,
                          timeout=timeout)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def check(label, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    if not ok and detail:
        print("       " + detail.replace("\n", "\n       "))
    return ok


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=60)


# ---------------------------------------------------------------------------
# S-25 用例名 ASCII 化
# ---------------------------------------------------------------------------

def test_slug():
    results = []
    proj = WORK / "slug_proj"
    (proj / ".ea").mkdir(parents=True, exist_ok=True)

    for desc in ("om100 指纹模块上电自检", "om100 电机控制自检", "led blink",
                 "LED/闪烁:测试*用例"):
        run([str(TEST_GEN), "board", desc], cwd=proj)

    made = sorted(p.name for p in (proj / ".ea" / "tests" / "board").glob("*_test.c"))
    results.append(check(
        "E1 board 用例名全部 ASCII 且有 4 个产物",
        len(made) == 4 and all(n.isascii() for n in made),
        f"实际: {made}"))

    results.append(check(
        "E2 同前缀的中文描述不互相覆盖（靠哈希后缀区分）",
        any(n.startswith("om100_") for n in made)
        and len({n for n in made if n.startswith("om100_")}) == 2,
        f"实际: {made}"))

    results.append(check(
        "E3 纯 ASCII 描述不受影响", "led_blink_test.c" in made,
        f"实际: {made}"))

    # 描述本身不能丢：文件名 ASCII 化，文档与固件注释里仍要是原文
    docs = list((proj / ".ea" / "tests" / "board").glob("*.md"))
    blob = "\n".join(d.read_text(encoding="utf-8") for d in docs)
    # 固件源码按 GBK 读 —— 它是要进 Keil 工程的，写 UTF-8 中文会在 IDE 里乱码
    srcs = "\n".join(p.read_text(encoding="gbk") for p in
                     (proj / ".ea" / "tests" / "board").glob("*.c"))
    results.append(check(
        "E4 中文描述仍保留在用例文档与固件头注释里（.c 为 GBK）",
        "指纹模块上电自检" in blob and "指纹模块上电自检" in srcs,
        f"docs={[d.name for d in docs]}"))
    return results


# ---------------------------------------------------------------------------
# S-23 tool_config CLI
# ---------------------------------------------------------------------------

def test_tool_config_cli():
    results = []
    cfg = SHARED / "tool_config.py"

    rc, out = run([str(cfg), "--help"])
    results.append(check("E5 tool_config --help 有内容", rc == 0 and "list" in out,
                         out[-200:]))

    rc, out = run([str(cfg)])          # 无参：此前一声不吭
    results.append(check("E6 无参运行给出用法而非静默",
                         "usage" in out.lower() and rc != 0, f"rc={rc} out={out[:200]}"))

    rc, out = run([str(cfg), "list"])
    results.append(check("E7 list 空配置时有明确输出", rc == 0 and "无已注册工具" in out,
                         f"rc={rc} {out[:200]}"))

    ws = WORK / "cfgws"
    ws.mkdir(exist_ok=True)
    rc, out = run([str(cfg), "set", "openocd", "C:/fake/openocd.exe", "--workspace", str(ws)])
    results.append(check("E8 set 写入成功", rc == 0 and "已注册" in out, out[:200]))

    rc, out = run([str(cfg), "get", "openocd", "--workspace", str(ws)])
    results.append(check("E9 get 读回同一路径", rc == 0 and "C:/fake/openocd.exe" in out
                         or "C:\\fake\\openocd.exe" in out, out[:200]))

    rc, out = run([str(cfg), "remove", "openocd", "--workspace", str(ws)])
    rc2, out2 = run([str(cfg), "get", "openocd", "--workspace", str(ws)])
    results.append(check("E10 remove 后 get 报未注册且退出非 0",
                         rc == 0 and rc2 == 1, f"rc={rc} rc2={rc2} {out2[:160]}"))
    return results


# ---------------------------------------------------------------------------
# S-21 detect_tools 自相矛盾
# ---------------------------------------------------------------------------

def test_detect_tools_reconcile():
    results = []
    ws = WORK / "dtws"
    ws.mkdir(exist_ok=True)

    # 四个工具全部预先注册到**探测逻辑绝无可能命中**的路径。
    # 这样"探测不到"与"配置里已有"必然同时成立 —— 正是报告里 logic2 的处境。
    cfg_dir = WORK / "appdata" / "ea_skill"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    names = ("uv4", "openocd", "jlink", "logic2")
    tools = {n: str(WORK / "nonexistent" / f"{n}.exe") for n in names}
    (cfg_dir / "config.json").write_text(json.dumps({"tools": tools}), encoding="utf-8")

    rc, out = run([str(SHARED / "detect_tools.py")], cwd=ws)

    m = re.search(r"结果: (\d+) 个自动找到并注册, (\d+) 个沿用已有配置, (\d+) 个需手动配置", out)
    found, reused, missing = (int(g) for g in m.groups()) if m else (-1, -1, -1)
    results.append(check(
        "E11 探测不到但已注册的工具算『沿用』，不算『需手动配置』",
        found + reused == len(names) and missing == 0, out[-700:]))
    # 报告的原文矛盾：同一个工具先被报"未自动找到"，紧接着又列在"当前已注册工具"里
    registered = out.split("当前已注册工具")[-1]
    not_found = [m.group(1) for m in re.finditer(r"^  ⬜ (\w+): 未找到", out, re.M)]
    results.append(check(
        "E12 同一工具不会既『未找到』又出现在已注册列表里",
        not [n for n in not_found if f"  {n}: " in registered],
        f"未找到={not_found}\n{out[-700:]}"))
    results.append(check(
        "E13 全部就绪（含沿用）时退出 0，不再误报要修环境",
        rc == 0, f"rc={rc} {out[-300:]}"))
    return results


# ---------------------------------------------------------------------------
# S-15 project_guard diff
# ---------------------------------------------------------------------------

def test_project_guard_diff():
    results = []
    guard = SHARED / "project_guard.py"
    proj = WORK / "guard_proj"
    (proj / ".ea").mkdir(parents=True, exist_ok=True)
    (proj / "startup_x.s").write_text("void Reset_Handler(void){\n  int a=1;\n}\n",
                                      encoding="utf-8")

    run([str(guard), "--snapshot", "--root", str(proj)])
    results.append(check("E14 snapshot 落盘内容副本",
                         (proj / ".ea" / "baseline" / "startup_x.s").is_file()))

    (proj / "startup_x.s").write_text("void Reset_Handler(void){\n  int a=2;\n}\n",
                                      encoding="utf-8")
    rc, out = run([str(guard), "--check", "--root", str(proj)])
    results.append(check("E15 --check 直接给出保护区 diff（不再只报文件名）",
                         "-  int a=1;" in out and "+  int a=2;" in out, out[-500:]))
    results.append(check("E16 未审批的保护区改动仍阻塞（退出码 2）", rc == 2, f"rc={rc}"))

    # GBK 源文件不能出乱码：Keil 工程多为 GB2312，UTF-8 读会整篇变替换字符
    src = proj / "src"
    src.mkdir(exist_ok=True)
    gbk = "你好".encode("gbk")
    (src / "cn.c").write_bytes(gbk + b"\n")
    run([str(guard), "--snapshot", "--root", str(proj)])
    (src / "cn.c").write_bytes(gbk + "!".encode("gbk") + b"\n")
    rc, out = run([str(guard), "--check", "--diff", "--root", str(proj)])
    results.append(check("E17 GBK 源文件的 diff 不乱码",
                         "你好" in out and "\ufffd" not in out, out[-400:]))

    # 基线副本缺失时要**明说**，不能静默给空 diff（那正是 S-15 的病根）
    shutil.rmtree(proj / ".ea" / "baseline", ignore_errors=True)
    rc, out = run([str(guard), "--check", "--diff", "--root", str(proj)])
    results.append(check("E18 无基线副本时明确说明，而非静默空 diff",
                         "无基线副本" in out, out[-400:]))
    return results


# ---------------------------------------------------------------------------
# S-16 git 前置检查
# ---------------------------------------------------------------------------

def test_git_state():
    results = []
    gs = SHARED / "git_state.py"

    # 父目录是仓库根 + 无提交 + 同级工程有改动（报告里的真实形态）
    parent = WORK / "gitparent"
    proj = parent / "proj"
    (proj / "src").mkdir(parents=True, exist_ok=True)
    (parent / "sibling").mkdir(parents=True, exist_ok=True)
    git(parent, "init", "-q", ".")
    (proj / "src" / "main.c").write_text("int main(){}\n", encoding="utf-8")
    (parent / "sibling" / "other.c").write_text("x\n", encoding="utf-8")

    rc, out = run([str(gs), "--root", str(proj), "--scope-only"])
    results.append(check("E19 无提交 → 明确指出 git diff 恒空", "还没有任何提交" in out,
                         out[-600:]))
    results.append(check("E20 仓库根≠工程目录 → 警告 git add . 会带上同级工程",
                         "不是**工程目录" in out or "不是" in out and "同级工程" in out,
                         out[-600:]))
    results.append(check("E21 不可用时给出 project_guard 替代路径并退出非 0",
                         rc == 1 and "project_guard" in out, f"rc={rc}"))
    results.append(check("E22 --scope-only 只列工程内的改动",
                         "proj/src/main.c" in out
                         and "sibling/other.c" not in out.split("──")[-1],
                         out[-500:]))

    # 正常仓库：根=工程目录且有提交
    ok = WORK / "gitok"
    (ok / "src").mkdir(parents=True, exist_ok=True)
    git(ok, "init", "-q", ".")
    git(ok, "config", "user.email", "t@t")
    git(ok, "config", "user.name", "t")
    (ok / "src" / "main.c").write_text("int main(){}\n", encoding="utf-8")
    git(ok, "add", "-A")
    git(ok, "commit", "-qm", "init")
    rc, out = run([str(gs), "--root", str(ok)])
    results.append(check("E23 正常仓库判为可用（退出 0）", rc == 0 and "可用" in out,
                         f"rc={rc} {out[-300:]}"))

    nogit = WORK / "nogit"
    nogit.mkdir(exist_ok=True)
    rc, out = run([str(gs), "--root", str(nogit)])
    results.append(check("E24 非 git 目录退出 2 并指向基线比对",
                         rc == 2 and "project_guard" in out, f"rc={rc}"))
    return results


def main() -> int:
    results = []
    for fn in (test_slug, test_tool_config_cli, test_detect_tools_reconcile,
               test_project_guard_diff, test_git_state):
        results += fn()
    total, passed = len(results), sum(results)
    print("-" * 50)
    print(f"{passed}/{total} 通过")
    shutil.rmtree(WORK, ignore_errors=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
