# 命令: /ea scope (示波器)

## 功能

两条路径：

**A. Rigol 示波器实时 SCPI** —— 抓取 + 测量，排查模拟波形/电压/频率。

**真机验证状态（两族都已实测，脚本按机型自动适配）**：

| 机型 | 固件 | 横向 | 屏幕点数 | 测量命令形式 | 深内存 |
|------|------|------|----------|--------------|--------|
| DS1074Z（DS1000Z 族） | `00.04.02.SP3` | 12 格 | 1200 | `:MEAS:ITEM? <item>,<ch>` | 不可用（`:ACQ:MDEP` 写不进） |
| DS2302A（DS2000A 族） | `00.03.06` | **14 格** | **1400** | **`:MEAS:<item>? <ch>`** | **可用（须先 `:STOP`）** |

两族**命令集不通用**：DS2000A 上 `:MEAS:ITEM` 这个节点根本不存在（`:SYST:ERR?` 回 `-113 Undefined header`）。脚本用 `*IDN?` 给初步猜测 + 600ms 短超时实测确认，猜错自动换另一种形式——见注意事项 15。横向格数不写死，取 `:WAV:POIN?/100`（两族都是 100 点/格）。

下列结论均在这两台机上实测得出，标注了实测数字——换固件/机型需重新复核。

| 能力 | 参数 | 底层 | 依赖 |
|------|------|------|------|
| 波形抓取 | `--capture` | `:WAV:DATA?` + TMC 解析 | pyvisa + pyvisa-py |
| 电压/时间轴还原 | `--capture` | `(code-YREF-YOR)*YINC` + `:WAV:XINC?` | numpy |
| 采样域分析 | `--capture` | 整数周期内算频率/占空比 + 削顶/平线/噪声/不等周期判定 | numpy |
| 直接测量 | `--measure vpp,freq,...` | 命令形式按机型探测（可与 `--capture` 同用） | pyvisa |
| 前端配置 | `--coupling/--vdiv/--voffs/--timebase/--autoset` | `:{ch}:COUP/SCAL/OFFS`、`:TIM:SCAL`、`:AUT` | pyvisa |
| CSV / PNG | `--save` / `--plot` | numpy + matplotlib | + matplotlib |
| 设备识别 | `--detect` | 列资源 + 逐个 `*IDN?` | pyvisa |
| 无硬件自测 | `--parse-tmc <file>` | 合成 TMC 块走解析链路 | numpy 即可 |

**退出码**：`0`=正常 / `1`=失败（含参数错误、文件不存在、连不上）/ `2`=取到数据但不可信（削顶、平线、噪声、某项测不出）。按 rc 分支时不要把 2 当失败丢弃——数据在，只是需要看 `warnings`。

SCPI 基本通用，主流品牌（Siglent/鼎阳/泰克等）命令集大同小异，换设备只需调 `--resource`。

**B. 正点原子 DS100 手持示波器（无 SCPI，U 盘导出 CSV）** —— 手动导出波形后解析分析。

| 能力 | 参数 | 底层 | 依赖 |
|------|------|------|------|
| 导出 CSV 波形解析 | `--parse-ds100 <file.csv>` | 跳过 header/自动探测列 + numpy 测量 | numpy 即可 |
| 采样率自动提取 | `--parse-ds100` | 正则解析 header `sampling rate : N` | numpy |
| 频率/周期/占空比 | `--parse-ds100` | 首末上升沿跨度法 + 整数周期占空比（与 Rigol 路径同源） | numpy |
| 波形出图 | `--plot` | matplotlib | + matplotlib |

DS100 限制：**不支持 SCPI、无官方 PC 软件**，USB 仅作 U 盘（卷标 `ATK-DSO`）。只能在示波器上按 **M → Save → CSV** 导出原始采样，再用本命令解析。

真机格式（已实测 `999.CSV`）：单行 header `CHA(V)  probe:X5,sampling rate : 10000`，后接**单列电压值**（每行一个，100000 点）。脚本自动从 header 提取采样率（10kHz）与探头倍率（x5），无需手动指定。

## 调用方式

```bash
py ~/.claude/skills/EA-SKILL/tools/instrument/scripts/scope.py --detect
# ✅ 确认 pyvisa 栈 / VISA 资源 / 设备

py .../scope.py --list            # 列出所有 VISA 资源

# 抓波形：CHAN1，存 CSV + 出图（--coupling DC 是测电压/占空比的前提）
py .../scope.py --capture --coupling DC --vdiv 1 --save .ea/captures/scope_S9.csv --plot scope.png

# 抓 + 测一条命令闭环（--capture 与 --measure 可同时给，同一次连接内完成）
py .../scope.py --capture --coupling DC --vdiv 1 --timebase 5e-5 --measure vpp,freq,pduty --json

# 直接测量（不抓波形）：Vpp / 频率 / 周期
py .../scope.py --measure vpp,freq,period

# 让仪器自己选档（发 :AUT，等 ~2s），再抓
py .../scope.py --capture --autoset --measure vpp,freq --json

# 深内存抓取（DS2302A 可用；窗口 xinc×点数 会告诉你实际拿到多长时间）
# 1400000 点 @2GSa/s = 700us，脚本自动 :STOP、自动放大 chunk 与超时
py .../scope.py --capture --mode RAW --points 1400000 --timebase 1e-4 --coupling DC --vdiv 1 --json

# 先确认探头档位与幅度是否匹配（输出里的 probe 字段 + Vpp 对不对）
py .../scope.py --capture --coupling DC --timebase 1e-4 --measure vpp

# 指定资源（USB / LAN）
py .../scope.py --capture --resource USB0::0x1AB1::0x04B0::DS2D000000::INSTR

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
| `--detect` | 是（动作） | 探测 pyvisa / 资源 / 设备，并**逐个资源试 `*IDN?`**（区分"插上了"与"能通上话"） |
| `--list` | 是（动作） | 列出所有 VISA 资源 |
| `--capture` | 是（动作，默认） | 抓波形主流程 |
| `--measure <items>` | 是（动作） | 内置测量，逗号分隔 `vpp,vmax,vmin,vtop,vbase,vamp,vrms,vavg,freq,period,pwidth,nwidth,pduty,nduty`。**单独给=只测量；与 `--capture` 一起给=同一连接内既抓又测** |
| `--parse-tmc <file>` | 是（动作） | 解析原始 TMC 块文件（自测） |
| `--parse-ds100 <file.csv>` | 是（动作） | 解析 DS100 导出 CSV（跳过 header，自动探测列） |
| `--resource` | 否 | VISA 资源串（缺省自动挑 RIGOL） |
| `--backend` | 否 | 后端：缺省**系统 VISA 优先**（USB-TMC 需系统后端/NI-VISA），无资源回退 `@py`（网口）；显式传则用指定后端 |
| `--timeout` | 否 | 查询超时 ms（默认 **3000**；RAW 读取时按 9 µs/点自动放宽）。两族机型对不认识的助记符**既不报错也不回复**，超时太短会把机型差异误报成链路故障 |
| `--chunk` | 否 | read_raw 分块字节（默认 32，DS1000Z USB 稳定性）。**RAW 读取期间自动提到 65536**——32 字节读 140 KB 要 6.16 s、1.4 MB 直接超时，见注意事项 3 |
| `--settle` | 否 | `:RUN` 后等波形就绪的秒数（默认 0.8）。改时基/档位后太短会抓到旧帧 |
| `--channel` | 否 | 通道（默认 CHAN1） |
| `--vdiv` | 否 | 垂直灵敏度 V/div（默认读 SCPI）。**给了 `--vdiv` 就把 `:OFFS` 归零**——DS1000Z 换档时会按比例缩放残留偏置（实测 0.5→1 V/div 把 −2.41 V 变成 −4.82 V），不归零会让上一次的偏置悄悄改变本次结果 |
| `--voffs` | 否 | 通道偏置 V，**写进仪器** `:{ch}:OFFS`（不是只在换算公式里加减）。缺省不动仪器当前值 |
| `--timebase` | 否 | 时基 s/div，写 `:TIM:SCAL` |
| `--coupling` | 否 | 通道耦合 `DC`/`AC`/`GND`。**测电压/占空比必须显式给 `DC`**——仪器出厂默认常是 AC，AC 把直流分量和低频占空比一起吃掉 |
| `--autoset` | 否 | 先发 `:AUT` 让仪器自动选档（DS1074Z 约 6.9 s，DS2302A 约 1.6 s），再抓取 |
| `--points` | 否 | 采样深度 `:ACQ:MDEP`（**仅 `--mode RAW` 有效**，NORM 只回屏幕点；给 NORM 会明确报错而不是崩在 numpy 里） |
| `--mode` | 否 | `NORM` 屏幕点（默认）/ `RAW` 深内存。**DS1074Z 上不可用**（`:ACQ:MDEP` 写不进）；**DS2302A 上可用，须先 `:STOP`**（脚本已自动做），见注意事项 4 |
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
1. --detect          pyvisa 栈 + VISA 资源 + 逐个 *IDN? 识别型号
2. 选资源             --resource 显式，或自动挑含 RIGOL 的
3. 前端配置           可选 :AUT(+*OPC? 等待) → :TIM:SCAL → :{ch}:COUP → :{ch}:SCAL → :{ch}:OFFS
                     每项写完都回读实际生效值（SCPI 写入会被仪器按档位取整）
4. 采集配置           :WAV:SOUR/:WAV:FORM BYTE/:WAV:MODE NORM  (+RAW 时 :ACQ:MDEP/:WAV:STAR/:WAV:STOP)
5. 采集               :RUN → 等 --settle（默认 0.8s）；RAW 时再 :STOP（深内存只能在停止态读）
6. 前导+抓取          :WAV:YREF?/YINC?/YOR? + :WAV:XINC?/XOR? → :WAV:DATA? 用 read_raw（二进制）→ parse_tmc_block
7. 还原               电压 (code-YREF-YOR)*YINC  →  analyze_samples 出频率/占空比
                     + 削顶/平线/噪声/不等周期(irregular)判定
8. 测量（可选）       形式自动探测：:MEAS:ITEM? VPP,CHAN1（DS1000Z）或 :MEAS:VPP? CHAN1（DS2000A）
                     直接读屏显同源值（失败重试 3 次，失败后读 :SYST:ERR? 补充原因）
9. 输出               --save CSV + --plot PNG + --json 摘要；有不可信项则 rc=2 并打印 warnings
```

**抓取为什么走 `:RUN` 而不是 `:SINGle`**：`:SINGle` 未触发成功时 `:WAV:DATA?` 回空块 `#0`（实测复现），
旧文档写的 `:TRIG:MOD SING → :SINGle → *OPC?` 与实际代码路径不符。自由运行 + 等 `--settle` 稳定得多。

示例闭环：怀疑 3.3V 供电纹波 → `--capture --coupling DC --channel CHAN1 --vdiv 0.1 --measure vpp,freq --json`
→ CSV/PNG + 采样域结果 + 仪器内置测量对照。

### 频率/占空比：看哪个数？

`--capture` 会同时给出两套独立结果，**不要默认它们一致**：

| 来源 | 字段 | 精度 | 说明 |
|------|------|------|------|
| 采样域（本工具算） | `sample_domain.freq_hz` / `duty_pct` | 高 | 只用首末上升沿之间的**整数个周期**，精度不受采样网格量化限制 |
| 仪器内置 DSP | `measurements.freq` / `pduty` | 低一档 | 按采样网格量化，时基越粗误差越大 |

实测（DS1074Z，5760 Hz / 10% 占空比方波，时基 5e-5）：

| 真值 | 采样域 | 仪器 `:MEAS:ITEM?` |
|------|--------|---------------------|
| 28800 Hz | 28802.9（+0.01%） | 28571.4（−0.79%） |
| 5760 Hz, 10% | 5763.7（+0.06%）, 10.09% | 5780.3（+0.35%）, 9.83% |
| 5760 Hz, 90% | 5763.7（+0.06%）, 89.91% | 5747.1（−0.22%）, 89.66% |

实测（DS2302A，同一路 PC10 信号，时基 1e-4，**42 个相位样本**）：

| 相位 | 真值 | 采样域 | 误差 | 占空比 |
|------|------|--------|------|--------|
| 0x55 | 28800 Hz | 28777.0 | −0.080% | 50.36% |
| 0x00 | 5760 Hz, 10% | 5755.4 | −0.080% | 10.07% |
| 0xFF | 5760 Hz, 90% | 5755.4 | −0.080% | 90.07% |
| 0xF0 | 5760 Hz, 50% | 5755.4 | −0.080% | 50.07% |

DS2302A 上误差是**恒定 −0.080%**（P1 的 28777.0 = 5755.4×5 精确成立），说明它来自
时基/波特率的系统性偏差而非算法——板子实际波特率约 57554（−0.08%），两台仪器测出的
偏差只差 0.02%，在时基精度指标之内。

结论：

- **频率优先信 `sample_domain`**，尤其在时基粗的时候——两条路径的差距是稳定可复现的（上表）。
- **占空比两者都可用**，实测都对到 ±0.35% 以内。哪边更准**没测出来**：做过一组跨时基
  （5e-5 / 2e-4）对照，有效样本太少、且抓到过 xinc 未刷新的样本，不足以排序，故不给结论。
- 两者都给出时互相印证；只有一个能给出时，`warnings` 会说明原因。
- 占空比还要看 `sample_domain.periods`：**窗口内不足 2 个完整周期时结果不可信**，
  加大 `--timebase` 让窗口覆盖更多周期。

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

⚠️ **频率测量的边界**：算法与 Rigol 路径同源——取过中值的上升沿，用**首末上升沿跨度 ÷ 周期数**
（不是逐周期取中位数，后者会被采样网格量化）。对**规则周期信号**准确；对**脉宽编码/抖动信号**
（间隔呈整数倍散列，如 PWM 遥控编码）给出的是跨度平均频率，需结合占空比/波形图判断。
占空比同样只在**整数个周期**内统计，`sample_domain.periods` 会告诉你实际用了几个周期——
这个数太小（<2）时占空比不可信。

## 注意事项

1. **必须 `read_raw`** 读 `:WAV:DATA?`，用 `query()` 会当 ASCII 解码抛 `UnicodeDecodeError`（脚本已用 read_raw）。
2. **TMC 块格式 `#NXXXXXX`**：N 位 ASCII 长度头，按长度精确切片，末尾 `\n` 终止符自动剔除。
3. **USB 传输参数**：`inst.chunk_size=32` + `timeout=3000`（DS1000Z 防 `Unexpected MsgID format`），脚本默认已设。
   **但 32 字节的块读不动深内存**——实测 DS2302A 读 140 KB：`chunk=32` 要 **6.16 s**，`chunk≥1024` 只要 **0.63 s**（差 10 倍），1.4 MB 在 32 下直接撞 3 s 超时读不完。脚本因此在 `--mode RAW` 读取期间临时把 chunk 提到 65536、超时按 9 µs/点放宽，读完还原（放大块不影响正确性：1024/65536/1048576 三种块大小读回的 sum/min/max 完全相同）。
4. **深内存（`--mode RAW`）两种机型结论相反**：
   - **DS1074Z 不可用**：`:ACQ:MDEP` **写入被忽略**（写 12000 / 120000 / AUTO 都读回 6000），`:WAV:STOP 12000` 被夹回 1200。RAW 只回 1200 点，但 `:WAV:XINC?` 变成 4e-8 而不是 2e-6 —— **时间轴是错的**（48 µs vs 真实 240 µs），静默按错时基换算出的频率最危险。
   - **DS2302A 可用，但必须 `:STOP`**：RUN 状态下不管怎么设 `:WAV:MODE RAW` 都只回屏幕 1400 点；`:STOP` 之后同一命令如实回 1400 / 14000 / 140000 / 700000 / 2800000 点。实测 STOP=2800000 读回 9 个上升沿、间隔 **173.745 µs**（真值 173.61，+0.08%），完全正确。脚本已自动 `:STOP` 再读（两种机型都这样走）。
     注意这台 `:ACQ:MDEP` 也**不可自由写**：它由时基反推（`SRAT=2 GSa/s`、tb=1e-4 → `MDEP=2800000`，恰好覆盖 1.4 ms 整屏），写 1200000 进去读回还是 1400000/2800000。脚本按**回读值**夹 `:WAV:STOP`，避免越界。
   - 脚本的两道防线对两族都生效：RAW 未拿到深内存时给 warning；**时间轴检查分模式**——NORM 下 `xinc×点数` 必须**等于** `时基×横向格数`，RAW 下 `xinc` 是深内存采样间隔（DS2302A 是 5e-10，屏幕是 1e-6），点数只是用户要的**子窗口**，只能检查有没有超出采集窗口。原先不分模式一律按"相等"判，RAW 下每次抓取都误报。
4b. **`--settle`（默认 0.8 s）**：改时基/档位后立刻 `:WAV:DATA?` 会抓到旧帧，这是"同一命令两次结果不同"的常见来源。时基很慢（>0.5 s/div）时手动加大。
5. **`:WAV:XOR?` 可能返回两个值**（`xorig,xref`），脚本取第一个。
6. **电压还原公式**：`V = (code − YREF − YOR) × YINC`，由 `:WAV:YREF?/YINC?/YOR?` 实测前导式给出，**不要写死中心 128 / 每格 25 LSB**。实测 DS1074Z（fw 00.04.02.SP3）上 `YREF = 127`（不是 128）、`YINC = vdiv/25`（1 V/div 时 0.04），且 **`YOR` 的单位是码值不是伏特**（`YOR = 25 × OFFS`）。三种偏置下该式与仪器自报的 VMIN/VPP **完全一致**：OFFS=0 → `(135−127−0)×0.04 = 0.32 V`；OFFS=+1 → `(160−127−25)×0.04 = 0.32 V`；OFFS=−1 → `(110−127+25)×0.04 = 0.32 V`，读数不随偏置漂移。旧公式 `(val−128)/25*vdiv+voffs` 在 OFFS=0 时偏低 1 个码值，且把 `voffs` 加在解码公式里而不是写进仪器，偏置越大错得越多。
6b. **仪器状态是持久的**：换 `:SCAL` 时 DS1000Z 会**按比例缩放残留 `:OFFS`**（实测 0.5→1 V/div 把 −2.41 V 变成 −4.82 V）。上一次跑留下的偏置会悄悄改变下一次的结果，甚至把 3.3 V 信号整体推出屏幕。脚本在给了 `--vdiv` 而未给 `--voffs` 时主动把 `:OFFS` 归零。
7. **USB 直连需驱动**：Windows 用 Zadig 给设备装 WinUSB（配 pyvisa-py），或装 NI-VISA 用系统后端（`--backend` 留空）。**脚本默认系统 VISA 优先**——装了 NI-VISA/RIGOL IVI 时直接识别 USB 设备，无需 Zadig。
8. **测不出的测量项：`rise` / `fall` 在两族都不可用**。DS1074Z fw 00.04.02.SP3 与 DS2302A fw 00.03.06 上 `RISE` / `RISetime` / `FALL` / `FALLtime` **全都不被识别**，而两台对不认识的助记符**既不报错也不回复**——主机只能阻塞到超时。这是个静默陷阱：`--measure` 会白等到超时才返回 N/A。脚本已把 `rise`/`fall` 移出默认名单，显式请求时直接报"该机型不支持"并建议改用采样域测量或 `pwidth`/`pduty` 推算。
   同理 `PERI` 超时而 `PERIod` 正常——助记符必须用长名。`PSLEWrate` 在 DS1074Z 可用，**DS2302A 上不可用**。
8b. **无有效测量时仪器返回哨兵值 `9.9E37`**，脚本按"无数据"处理成 `null` 并让 rc=2，不会把它当成一个数。
8c. **`:MEAS:ITEM?` 查询失败要重试**：刚改完前端（`:SCAL`/`:COUP`）时仪器可能不回答，重试 3 次即可，别把一次超时当成命令不存在。脚本每次查询失败后都会 **drain 读缓冲**——超时不等于不会回，迟到回复会占住缓冲让后续每次读取错位一格（实测 `:CHAN1:OFFS?` 因此读到了 `:CHAN1:COUP?` 的 `'DC'`，直接抛 `could not convert string to float: 'DC'`）。
8d. **长命令用 `*OPC?` 等，不要轮询**：`*OPC?` 在两台都可用且真的会等（DS1074Z 上 `:AUT` 后 6.9 s 才回 `'1'`，DS2302A 上 1.59 s），之后缓冲是干净的。轮询 `:TIM:SCAL?` 会攒下 6 个迟到回复，AUT 结束后一次性涌入，此后每次读取都错位一格。
8e. **`:SYST:ERR?` 只有 DS2000A 能用，但它能把"超时"变成确定诊断**：DS2302A 上查询失败后读 `:SYST:ERR?` 得到 `-113,"Undefined header; keyword cannot be found"`，直接证明该命令节点不存在；DS1000Z 上这条不回。脚本按 600 ms 短超时探一次并缓存，只在该命令可用时才用它补充错误原因。
9. **两族坐标系统实测相同**（原先标注的"DS2000A 需复核"已撤销）：`YREF=127`、`YINC=vdiv/25`（1/0.5/0.2 V/div 三档比值都是 1.0000）、`YOR=25×OFFS`（码值）。DS2302A 上 `:MEAS:VMIN?/VMAX?/VPP?` 与 `(code−YREF−YOR)×YINC` 解码的比值**恰好 1.000**（1 / 2 / 0.5 / 0.2 / 0.15 V/div 五档都验过）。
9b. **探头档位会被仪器的 `:PROB?` 掩盖**：实测踩过——探头**物理档位在 10×**，而 `:CHAN1:PROB?` 仍回 1，于是一个 3.48 V 的 3.3 V 逻辑高电平被读成 **0.35 V**，输出里没有任何字段能看出问题。脚本现在回读并输出 `probe` 字段（人类可读行显示 `PROB×1`），且当 Vpp < 0.6 V 且 `:PROB?`=1 时给出"核对探头 1×/10× 开关"的警告。切换物理档位后实测 3.48 V，与预期一致。
     **该警告有门限，不是"小信号就报"**：只在该波形**本来够高**时触发（`25×Vpp/vdiv ≥ FLAT_CODE_SPAN`，即至少占 8 个码值）。依据是被 10× 压过的 3.3 V 逻辑信号在 1 V/div 上仍占约 8 个码值，而真正的微小纹波只占几个码值——后者是"信号本来就小"，那是平线警告的活。实测还原后的固件 PC10 只有 0.16 V 纹波（码值跨度 4），两条警告曾同时出现且探头那条是错的，加门限后只剩正确的平线警告。
9c. **时间类测量失败往往不是"超量程"而是"幅度太小"**：实测 DS2302A 上 0.35 Vpp 打在 1 V/div（纵向仅 0.35 格）时 `:MEAS:VPP?` 正常回数，而 `FREQ`/`PDUTy`/`PWIDth` **全部回 9.9E37 哨兵**；降到 0.5 V/div 就都正常。脚本的哨兵提示已列出这个成因，并在"时间类测量为 null 而 VPP 正常"时直接算纵向占比、给出建议的 `--vdiv`（让波形占 3 格左右）。

15. **测量命令形式按机型探测，不要写死**：DS1000Z 用 `:MEAS:ITEM? <item>,<ch>`，DS2000A **没有 `:MEAS:ITEM` 这个节点**（`-113 Undefined header`），只有直查形式 `:MEAS:<item>? <ch>`。两族形式互斥且都不报错，所以脚本按 `*IDN?` 给初步猜测，再用 600 ms 短超时发一次 VPP 查询**实测确认**，不对就换另一种——猜错只多花一次 0.6 s 超时，不会产出错数字。探测结果在进程内缓存，每次运行只探一次。
16. **上升沿间隔不均匀时频率无意义**：跨度法（首末上升沿÷周期数）隐含"信号等周期"这个前提。跨相位切换、突发、含毛刺的窗口会给出一个**看似自信的假频率**——实测 DS2302A 深内存 700 µs 窗口跨过信号发生器相位切换点时，上升沿间隔从 2 到 288 个采样点不等，仍算出 "7674 Hz" 这种没有意义的值。
    脚本现在算上升沿间隔的**变异系数**（std/mean），超过 `0.15` 就置 `sample_domain.irregular=true` 并警告 `freq_hz`/`duty_pct` 只是窗口平均值。阈值依据：真机 42 个相位样本实测 cv 最大 0.0279（P1，周期只有 34.7 个采样点，量化占比最大），有 5 倍余量；合成波形里跨相位切换是 0.82、2 采样点窄毛刺是 0.18，都被挡住。**边沿少于 3 个时该判据不适用**，此时由 `window-mean` 占空比警告覆盖。

**DS100 专属**：
10. **无 SCPI / 无 PC 软件**：不能 `--capture`/`--measure` 实时控制，只能 `--parse-ds100` 解析 U 盘 CSV。
11. **CSV 格式无官方文档**：脚本按「跳过 header + 自动探测列 + 提取采样率」弹性解析；真机格式如有出入，用 `--time-col/--volt-col` 校准并反馈补充。
12. **时间轴来源优先级**：显式 `--xinc/--sample-rate` > header `sampling rate` > 默认 1us。header 无采样率且未手动指定时，频率/时长会按 1us/点错算。
13. **导出操作**：示波器上 M 键 → Save → CSV（不是 BMP，BMP 只是截图无法做数值分析）。U 盘根目录的 `*.BMP`（480×320）是屏幕截图。
14. **编码信号频率仅参考**：规则方波准；脉宽编码信号（间隔散列）频率为中位数估计。

## 常见错误

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| No module named 'pyvisa' | 没装 pyvisa/pyvisa-py | `deps_check.py --install` |
| 无可用 VISA 资源 | 示波器未插 / 驱动缺失 / 后端缺失 | 插线 + Zadig 装 WinUSB；`--backend` 换系统 VISA |
| *IDN? 非 RIGOL | 自动挑错资源 | `--resource` 显式指定正确设备 |
| UnicodeDecodeError | 误用 query 读波形 | 脚本已用 read_raw，若自改代码勿用 query |
| Unexpected MsgID format | USB 读块超时 | `--chunk 32 --timeout 3000`（默认已设） |
| TMC 块头错误 | `#` 校验失败 / 数据截断 | 检查传输完整性；`--parse-tmc` 自测解析路径 |
| TMC 回空块 `#0` | `:SINGle` 未触发成功 | 本工具走 `:RUN` + `--settle`，不会遇到；自写代码时别用 `:SINGle` |
| `could not convert string to float: 'DC'` | 超时后的迟到回复让读缓冲错位一格 | 查询失败后 drain 缓冲（脚本已做）；长命令用 `*OPC?` 等，别轮询 |
| `--measure rise/fall` 返回 N/A | 两族机型都不支持这两个测量项 | 用采样域测量，或 `pwidth`/`pduty` 推算（见注意事项 8） |
| `--measure` 某项返回 null | 仪器回了 `9.9E37` 哨兵值＝该项当前测不出 | 看 `warnings`；确认 `--coupling DC`、信号在屏幕内 |
| `--measure` 全部项都超时 | 测量命令形式不对（DS2000A 无 `:MEAS:ITEM`） | 脚本自动探测，见注意事项 15；错误队列里的 `-113` 是确诊依据 |
| `--channel CHAN3` 报 `VI_ERROR_TMO` | 该机型没有这个通道（DS2000A 只有 2 通道）；写 `:CHAN3:SCAL` 不报错，要到后面查询才超时 | 脚本已前置探测并给出明确报错（CHAN1/2 不额外往返，CHAN3/4 才探）|
| 超时后紧接着的 `:SYST:ERR?` 也超时一次 | 主机侧超时会让仪器把 `-410,"Query INTERRUPTED"` 排进错误队列，排空前读不到真实条目 | 再读一次即可；脚本探测后显式 drain，且**不缓存"不支持"的结论**（一次抖动不足以定性）|
| 时间类测量全 null 但 vpp 正常 | 幅度相对 V/div 太小，纵向占比不足 | 调小 `--vdiv` 让波形占 3 格左右（见注意事项 9c） |
| 读到的电压比预期小一个数量级 | 探头物理档位在 10× 而 `:PROB?` 仍是 1 | 核对探头 1×/10× 开关；输出里的 `probe` 字段能看出来 |
| 电压比屏显偏低 1 个码值 | 用了写死的中心 128 | 脚本已改用 `:WAV:YREF?` 实测值（两族都是 127） |
| 同一命令两次结果不同 | 上次跑残留的 `:OFFS` 被换档按比例缩放 | 给 `--vdiv`（脚本自动把 `:OFFS` 归零），或显式 `--voffs` |
| 频率对但占空比离谱 | 窗口内不足一个完整周期 | 加大 `--timebase` 让窗口覆盖更多周期，看 `sample_domain.periods` |
| 频率看着正常但无意义 | 信号不等周期（跨相位/突发/毛刺），跨度法给的是平均值 | 看 `sample_domain.irregular` 与 `period_cv`（见注意事项 16） |
| RAW 请求 N 点只回屏幕点数 | 仪器不在 `:STOP` 状态，或该机型 `:ACQ:MDEP` 不可写 | 脚本已自动 `:STOP`；DS1074Z 请改用 `--mode NORM`（见注意事项 4） |
| RAW 大块读取超时 | `--chunk 32` 读不动深内存 | 脚本在 RAW 下自动提到 65536 并放宽超时（见注意事项 3） |
| RAW 窗口内是平线 | 深内存窗口可能短于信号周期（140000 点 @2 GSa/s 只有 70 µs） | 加大 `--points`；脚本会提示窗口时长 |
| DS100 CSV 无数值行 | 文件不是波形导出 / 编码异常 | 确认 M→Save→CSV 导出；`--time-col/--volt-col` 指定 |
| DS100 频率异常 | header 无采样率且未手动指定，误用 1us/点 | `--sample-rate` 提供真实采样率，或确认 header 含 `sampling rate` |

## 相关文件
- `~/.claude/skills/EA-SKILL/tools/instrument/scripts/scope.py` - 工具脚本
- `~/.claude/skills/EA-SKILL/tools/instrument/scripts/deps_check.py` - 依赖探测
- `~/.claude/skills/EA-SKILL/tools/instrument/requirements.txt` - 依赖清单
- `commands/la.md` - 逻辑分析仪命令（同类仪器）
