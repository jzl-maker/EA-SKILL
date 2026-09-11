#!/usr/bin/env python
"""OpenOCD 探针探测（共享实现）。

这段判据一度被复制到三个脚本里各一份 —— `flash-openocd` / `serial-monitor` /
`jlink-debug` —— 而且三份**都是错的**：判据是「退出码 0 **或**输出含探针关键词」，
可失败输出里恰好含那些词：

    Error: unable to find a matching CMSIS-DAP device   → 含 "cmsis-dap"
    Error: No J-Link device found                       → 含 "j-link"

于是**没找到设备反而被判成已连接**，调用方据此自动选中一个不存在的探针、阻塞到
超时，再把超时归因到接线/供电 —— 正是 `flash.md` 那条经验警告想避免的方向。

代价不是"报个错"那么轻：它会把排查引向硬件，而问题在软件。修一处漏两处的账
已经付过一次，故集中到这里。
"""

from __future__ import annotations

import subprocess

# init 失败的标志。命中任一条即判「该接口无设备」，**与输出里出现了什么关键词无关**。
FAILURE_MARKERS = (
    "error:", "no device found", "unable to find", "cannot find",
    "open failed", "failed to open", "libusb_error", "unsupported transport",
)


def first_evidence_line(text: str) -> str | None:
    """取第一行错误证据（供复核），过长则截断。"""
    for line in text.splitlines():
        s = line.strip()
        if s and any(m in s.lower() for m in FAILURE_MARKERS):
            return s if len(s) <= 120 else s[:117] + "..."
    return None


def probe_interfaces(openocd_exec: str, configs: dict[str, str], priority: list[str],
                     timeout: int = 8) -> list[tuple[str, bool, str | None]]:
    """逐接口跑一次 `init; exit`，返回 `[(接口, 是否连接, 未连接的原因)]`。

    判据是「退出码为 0 **且** 输出里没有错误标志」。原因字符串取自原始输出的证据
    行 —— 不做二次转述，AI 才有东西可复核。
    """
    results: list[tuple[str, bool, str | None]] = []
    for interface in priority:
        cfg = configs[interface]
        try:
            res = subprocess.run([openocd_exec, "-f", cfg, "-c", "init; exit"],
                                 capture_output=True, text=True, timeout=timeout)
        except Exception as exc:
            results.append((interface, False, f"探测异常（{type(exc).__name__}）"))
            continue

        combined = f"{res.stdout}\n{res.stderr}"
        lowered = combined.lower()
        if res.returncode == 0 and not any(m in lowered for m in FAILURE_MARKERS):
            results.append((interface, True, None))
        else:
            results.append((interface, False,
                            first_evidence_line(combined) or f"退出码 {res.returncode}"))
    return results


def detect_probes(openocd_exec: str, configs: dict[str, str], priority: list[str],
                  with_evidence: bool = False) -> list[str]:
    """已连接的探针列表。`with_evidence=True` 时打印每个接口的判定依据。"""
    results = probe_interfaces(openocd_exec, configs, priority)
    if with_evidence:
        for interface, ok, reason in results:
            print(f"    - {interface}: 已连接" if ok
                  else f"    - {interface}: 未连接（{reason}）")
    return [interface for interface, ok, _ in results if ok]


def detect_first_probe(openocd_exec: str, configs: dict[str, str],
                       priority: list[str]) -> str | None:
    """按优先级返回第一个已连接的探针。"""
    for interface, ok, _ in probe_interfaces(openocd_exec, configs, priority):
        if ok:
            return interface
    return None
