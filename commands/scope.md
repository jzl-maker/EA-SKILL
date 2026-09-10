# 命令: /ea scope (示波器)

## 功能

两条路径：

**A. Rigol 示波器实时 SCPI** —— 抓取 + 测量，排查模拟波形/电压/频率。
**已真机验证：DS1074Z（DS1000Z 系列）。DS2000A 系列（如 DS2302A）命令集通用，但电压换算公式需真机复核**（DS2000A 坐标系统不同，见注意事项）。

| 能力 | 参数 | 底层 | 依赖 |
|------|------|------|------|
| 波形抓取 | `--capture` | `:WAV:DATA?` + TMC 解析 | pyvisa + pyvisa-py |
| 电压/时间轴还原 | `--capture` | `(val-128)/25*vdiv+voffs` + `:WAV:XINC?` | numpy |
| 直接测量 | `--measure vpp,freq,...` | `:MEAS:ITEM?` | pyvisa |
| CSV / PNG | `--save` / `--plot` | numpy + matplotlib | + matplotlib |
| 设备识别 | `--detect` | `*IDN?` | pyvisa |
| 无硬件自测 | `--parse-tmc <file>` | 合成 TMC 块走解析链路 | numpy 即可 |

SCPI 基本通用，主流品牌（Siglent/鼎阳/泰克等）命令集大同小异，换设备只需调 `--resource`。

**B. 正点原子 DS100 手持示波器（无 SCPI，U 盘导出 CSV）** —— 手动导出波形后解析分析。

| 能力 | 参数 | 底层 | 依赖 |
|------|------|------|------|
| 导出 CSV 波形解析 | `--parse-ds100 <file.csv>` | 跳过 header/自动探测列 + numpy 测量 | numpy 即可 |
| 采样率自动提取 | `--parse-ds100` | 正则解析 header `sampling rate : N` | numpy |
| 频率/周期/占空比 | `--parse-ds100` | 过中值上升沿检测 + 阈值占比 | numpy |
| 波形出图 | `--plot` | matplotlib | + matplotlib |

DS100 限制：**不支持 SCPI、无官方 PC 软件**，USB 仅作 U 盘（卷标 `ATK-DSO`）。只能在示波器上按 **M → Save → CSV** 导出原始采样，再用本命令解析。

真机格式（已实测 `999.CSV`）：单行 header `CHA(V)  probe:X5,sampling rate : 10000`，后接**单列电压值**（每行一个，100000 点）。脚本自动从 header 提取采样率（10kHz）与探头倍率（x5），无需手动指定。

## 调用方式

```bash
py ~/.claude/skills/EA-SKILL/tools/instrument/scripts/scope.py --detect
# ✅ 确认 pyvisa 栈 / VISA 资源 / 设备

py .../scope.py --list            # 列出所有 VISA 资源

# 抓波形：CHAN1，存 CSV + 出图
py .../scope.py --capture --channel CHAN1 --save .ea/captures/scope_S9.csv --plot scope.png

# 直接测量（不抓波形）：Vpp / 频率 / 周期
py .../scope.py --measure vpp,freq,period

# 指定资源（USB / LAN）
py .../scope.py --capture --resource USB0::0x1AB1::0x04CE::DS1ZA000000::INSTR

# 无硬件自测：解析合成 TMC 块（验证解析/还原/出图全链路）
py .../scope.py --parse-tmc raw.bin --vdiv 1.0 --xinc 1e-6 --save out.csv --plot out.png

# 纯逻辑核对（打印将执行的 SCPI 序列，不连设备）
py .../scope.py --dry-run --capture

# ── 正点原子 DS100（U 盘导出 CSV）──
# 解析用户导出的 CSV，输出频率/幅值/周期/占空比 + 出图
py .../scope.py --parse-ds100 E:/DS100_DATA/latest.csv --plot ds100.png --save norm.csv

# 单列电压 CSV：用采样率推断时间轴（DS100 常导出单列原始电压）
py .../scope.py --parse-ds100 wave.csv --sample-rate 1000000 --json

# CSV 列不是默认 time,voltage 时显式指定（0 基）
py .../scope.py --parse-ds100 wave.csv --time-col 0 --volt-col 2 --plot wave.png
```

## 参数表

| 参数 | 必须 | 说明 |
|------|------|------|
| `--detect` | 是（动作） | 探测 pyvisa / 资源 / 设备 |
| `--list` | 是（动作） | 列出所有 VISA 资源 |
| `--capture` | 是（动作，默认） | 抓波形主流程 |
| `--measure <items>` | 是（动作） | 直接测量，逗号分隔 `vpp,freq,period,vmax,vmin,vrms,vavg,rise,fall,...` |
| `--parse-tmc <file>` | 是（动作） | 解析原始 TMC 块文件（自测） |
| `--parse-ds100 <file.csv>` | 是（动作） | 解析 DS100 导出 CSV（跳过 header，自动探测列） |
| `--resource` | 否 | VISA 资源串（缺省自动挑 RIGOL） |
| `--backend` | 否 | 后端：缺省**系统 VISA 优先**（USB-TMC 需系统后端/NI-VISA），无资源回退 `@py`（网口）；显式传则用指定后端 |
| `--timeout` | 否 | 查询超时 ms（默认 1500） |
| `--chunk` | 否 | read_raw 分块字节（默认 32，DS1000Z USB 稳定性） |
| `--channel` | 否 | 通道（默认 CHAN1） |
| `--vdiv` | 否 | 垂直灵敏度 V/div（默认读 SCPI） |
| `--points` | 否 | 采样深度 `:ACQ:MDEP` |
| `--mode` | 否 | `NORM` 屏幕点（默认）/ `RAW` 深内存 |
| `--xinc` | 否 | 每采样点时间间隔 s（`--parse-tmc` / DS100 单列 CSV 用，默认 1us） |
| `--sample-rate` | 否 | 采样率 Hz（DS100 便捷写法，等价 `--xinc 1/rate`，两者互斥） |
| `--time-col` | 否 | DS100 CSV 时间列（0 基，默认自动探测） |
| `--volt-col` | 否 | DS100 CSV 电压列（0 基，默认自动探测） |
| `--save <file.csv>` | 否 | 波形 CSV（time_s,voltage_v 两列） |
| `--plot <file.png>` | 否 | 波形图 png |
| `--dry-run` | 否 | 只打印将执行的调用序列，不连接/不解析 |
| `--json` | 否 | 结构化 JSON 输出（供 AI 解析） |

## 工作流程（AI 执行闭环）

```
1. --detect          pyvisa 栈 + VISA 资源 + *IDN? 识别型号
2. 选资源             --resource 显式，或自动挑含 RIGOL 的
3. 配置               :WAV:SOUR/:WAV:FORM BYTE/:WAV:MODE NORM + :ACQ:MDEP
4. 触发               :TRIG:MOD SING → :SINGle → *OPC?
5. 抓取               :WAV:DATA? 用 read_raw（二进制）→ parse_tmc_block
6. 还原               电压 (val-128)/25*vdiv+voffs + 时间轴 :WAV:XINC?/:WAV:XOR?
7. 输出               --save CSV + --plot PNG + --json 摘要
8. 测量（可选）       :MEAS:ITEM VPP,CHAN1 → :MEAS:ITEM? 直接读屏显同源值
```

示例闭环：怀疑 3.3V 供电纹波 → `--capture --channel CHAN1 --vdiv 0.1 --measure vpp,freq` → CSV/PNG + 测量值对照屏显。

### DS100 工作流（AI 执行闭环）

```
1. 探测        ls E:/（卷标 ATK-DSO 即 DS100 U 盘；根目录 BMP 截图 + *.CSV）
2. 导出        DS100 无 CSV 时 → 提示用户在示波器上 按 M → Save → CSV 导出一组
3. 定位        CSV 在 U 盘根目录（如 999.CSV，大小数百 KB）
4. 解析        --parse-ds100 <file.csv> --plot <png>
5. 输出        采样率/探头倍率（自动提取）+ 频率/周期/占空比/幅值 + 波形图
```

DS100 CSV 为**原始采样电压值**。脚本弹性解析：自动跳过 header 行、自动提取 header 里的
`sampling rate`（采样率）与 `probe`（探头倍率）构建时间轴；列结构异常时用
`--time-col/--volt-col` 显式指定；header 无采样率时才需手动 `--sample-rate`/`--xinc`。
官方建议 Matlab/Excel 分析，本命令替代之。

⚠️ **频率测量的边界**：过中值上升沿间隔取中位数，对**规则周期信号**准确；对
**脉宽编码/抖动信号**（间隔呈整数倍散列，如 PWM 遥控编码）只给出参考中值频率，需结合
占空比/波形图判断。

## 注意事项

1. **必须 `read_raw`** 读 `:WAV:DATA?`，用 `query()` 会当 ASCII 解码抛 `UnicodeDecodeError`（脚本已用 read_raw）。
2. **TMC 块格式 `#NXXXXXX`**：N 位 ASCII 长度头，按长度精确切片，末尾 `\n` 终止符自动剔除。
3. **USB 稳定性**：`inst.chunk_size=32` + `timeout=1500`（DS1000Z 防 `Unexpected MsgID format`），脚本默认已设。
4. **`:WAV:MODE NORM` 只有屏幕 ~1200 点**；深内存用 `RAW`（需分块读，v1 未做分块，先 NORM）。
5. **`:WAV:XOR?` 可能返回两个值**（`xorig,xref`），脚本取第一个。
6. **电压还原公式写死**：BYTE 格式 8bit 无符号，中心 128，每格 25 LSB；`vdiv`/`voffs` 从 `:CHAN1:SCAL?`/`:CHAN1:OFFS?` 读取。已真机对照 DS1074Z：`--capture` 与 `--measure` 屏显 Vpp 差 ≤ 0.04V（1 LSB，窗口差异非公式错误）。
7. **USB 直连需驱动**：Windows 用 Zadig 给设备装 WinUSB（配 pyvisa-py），或装 NI-VISA 用系统后端（`--backend` 留空）。**脚本默认系统 VISA 优先**——装了 NI-VISA/RIGOL IVI 时直接识别 USB 设备，无需 Zadig。
8. **DS2000A（DS2302A）电压需复核**：命令集与 DS1000Z 通用（`:CHAN1:` 前缀、`:WAV:/:MEAS:` 一致），但其坐标系统与 DS1000Z 不同（实测 DS1074Z `YREF=127` 且字节中心 128，DS2000A 可能不同）。**首次接 DS2000A 必须对照屏显复核电压**：`--capture` 的 Vpp 若与屏显差 >2 LSB，按 `:WAV:YOR?/YINC?/YREF?` 实测值校准解码公式并反馈。

**DS100 专属**：
8. **无 SCPI / 无 PC 软件**：不能 `--capture`/`--measure` 实时控制，只能 `--parse-ds100` 解析 U 盘 CSV。
9. **CSV 格式无官方文档**：脚本按「跳过 header + 自动探测列 + 提取采样率」弹性解析；真机格式如有出入，用 `--time-col/--volt-col` 校准并反馈补充。
10. **时间轴来源优先级**：显式 `--xinc/--sample-rate` > header `sampling rate` > 默认 1us。header 无采样率且未手动指定时，频率/时长会按 1us/点错算。
11. **导出操作**：示波器上 M 键 → Save → CSV（不是 BMP，BMP 只是截图无法做数值分析）。U 盘根目录的 `*.BMP`（480×320）是屏幕截图。
12. **编码信号频率仅参考**：规则方波准；脉宽编码信号（间隔散列）频率为中位数估计。

## 常见错误

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| No module named 'pyvisa' | 没装 pyvisa/pyvisa-py | `deps_check.py --install` |
| 无可用 VISA 资源 | 示波器未插 / 驱动缺失 / 后端缺失 | 插线 + Zadig 装 WinUSB；`--backend` 换系统 VISA |
| *IDN? 非 RIGOL | 自动挑错资源 | `--resource` 显式指定正确设备 |
| UnicodeDecodeError | 误用 query 读波形 | 脚本已用 read_raw，若自改代码勿用 query |
| Unexpected MsgID format | USB 读块超时 | `--chunk 32 --timeout 1500`（默认已设） |
| TMC 块头错误 | `#` 校验失败 / 数据截断 | 检查传输完整性；`--parse-tmc` 自测解析路径 |
| DS100 CSV 无数值行 | 文件不是波形导出 / 编码异常 | 确认 M→Save→CSV 导出；`--time-col/--volt-col` 指定 |
| DS100 频率异常 | header 无采样率且未手动指定，误用 1us/点 | `--sample-rate` 提供真实采样率，或确认 header 含 `sampling rate` |

## 相关文件
- `~/.claude/skills/EA-SKILL/tools/instrument/scripts/scope.py` - 工具脚本
- `~/.claude/skills/EA-SKILL/tools/instrument/scripts/deps_check.py` - 依赖探测
- `~/.claude/skills/EA-SKILL/tools/instrument/requirements.txt` - 依赖清单
- `commands/la.md` - 逻辑分析仪命令（同类仪器）
