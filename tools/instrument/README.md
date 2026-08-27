# EA-SKILL instrument 仪器工具

两个测量仪器命令的底层工具簇：逻辑分析仪（Saleae）+ 示波器（Rigol）。

| 脚本 | 命令 | 设备 | 底层 |
|------|------|------|------|
| `scripts/logic_analyzer.py` | `/ea la` | Saleae Logic 8 / Pro 8 / Pro 16 | Logic 2 Automation API（`logic2-automation`） |
| `scripts/scope.py` | `/ea scope` | Rigol DS1000Z 系列 | PyVISA + SCPI over USB/LAN |

## 安装

```bash
# pip 依赖（logic2-automation / pyvisa / pyvisa-py / numpy / matplotlib）
py .../scripts/deps_check.py --detect
py .../scripts/deps_check.py --install
```

⚠️ 两个非 pip 前置条件：

1. **Saleae**：必须安装 **Logic 2 GUI 软件**（非 Python 包）。官网下载或 `winget install Saleae.Logic2`。`logic2-automation` 只是客户端，抓取时通过 gRPC 连后台 Logic 2，需在软件设置中开启「脚本服务器」（默认端口 10430），或给脚本传 `--launch` 自动拉起。
2. **Rigol**：示波器 USB 直连需 USB-TMC 驱动。Windows 可用 [Zadig](https://zadig.akeo.ie/) 给设备装 WinUSB 驱动（配合 `pyvisa-py` 纯 Python 后端），或安装 NI-VISA 用系统后端（脚本 `--backend @py` 失败会自动提示）。

## 无硬件自测

- 逻辑分析仪：`--simulate`（Logic 2 内置模拟设备，抓取/解码管线照跑）
- 示波器：`--parse-tmc <合成块文件>` 直接喂 TMC 二进制块验证解析链路，无需设备

## 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| `gRPC ... failed to connect` | Logic 2 没跑或脚本端口没开 | 打开 Logic 2 → 设置 → 开启脚本服务器；或 `--launch` |
| `No module named 'saleae'` | 没装 logic2-automation | `deps_check.py --install` |
| `list_resources()` 空 | 示波器没插 / USB-TMC 驱动缺失 | Zadig 装 WinUSB 或装 NI-VISA |
| `Unexpected MsgID format` | DS1000Z USB 读块超时 | `--chunk 32 --timeout 1500`（脚本默认已设） |

## 相关文件

- `scripts/common.py` — 共享 harness（控制台/JSON/依赖/输出目录）
- `scripts/deps_check.py` — 依赖与后端探测（`/ea setup` 调用）
- `commands/la.md` — `/ea la` 命令文档
- `commands/scope.md` — `/ea scope` 命令文档
