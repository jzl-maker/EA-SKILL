# 命令: /ea svd (寄存器地图)

## 功能

解析芯片 **SVD 寄存器描述文件**（CMSIS-SVD，Keil Pack 自带），把「门牌号（外设基地址）+ 住户名单（寄存器/位域）」查出来，还能**免查手册**读寄存器当前值并解码位域含义。

| 能力 | 说明 | 数据来源 |
|------|------|----------|
| 发现 SVD | 从 Keil Pack 目录自动扫描全部芯片 SVD | `ARM\PACK\*\*\svd\*.svd` |
| 外设清单 | 列出某外设下所有寄存器 + 地址偏移 | SVD `peripheral/registers` |
| 寄存器详情 | 地址、大小、读写属性、位域（bit 范围/含义/枚举）| SVD `register/fields/field` |
| 位域解码 | 读当前值后按位域展开：`GPIOx_PL_CFG[1:0]=01` | SVD `enumeratedValues` |
| 读当前值 | **免断点**直接读寄存器（OpenOCD TCL `mdw`，运行中读，不 halt）| OpenOCD / J-Link |

**用途**：调试时"这个寄存器现在是什么值 / 某位是不是置位 / 这个外设怎么配置的"——不必翻 1500 页参考手册，`/ea svd` 直接给出寄存器地图 + 实时值。

## 调用方式

```bash
py <SKILL>/tools/svd/scripts/svd_tool.py --detect
# → 发现 59 个 SVD（N32G4FR、STM32F4x1 等），用 --chip 匹配

# ① 外设下所有寄存器（门牌号）
py .../svd_tool.py --chip N32G4FR --periph GPIOA
# → GPIOA base 0x40010800 + 9 个寄存器（GPIOx_PL_CFG/PID/POD/… 各带偏移）
#   ⚠️ N32 寄存器命名与 STM32 不同（如 GPIO 是 GPIOx_PL_CFG 而非 MODER），以 SVD 输出为准

# ② 单个寄存器详情（住户名单：位域）
py .../svd_tool.py --chip N32G4FR --reg GPIOA GPIOx_PL_CFG
# → 0x40010800，位域 PMODE0[1:0]/PCFG0[3:2]… 各带 mask，可查枚举含义

# ③ 读当前值 + 位域解码（接好 ST-Link/DAP，免断点）
py .../svd_tool.py --chip N32G4FR --read RCC CR --backend openocd
# → RCC.CR = 0x0100XX00，按位域展开 HSEON/HSEONRDY…

# ④ 模糊搜索（记不全名字时）—— 子串匹配，不区分大小写
py .../svd_tool.py --chip N32G4FR --find TIM       # → TIM1..TIM8
py .../svd_tool.py --chip N32G4FR --find PID
# 搜不到时会给近邻建议，例如 --find timer → 建议 TIM8/TIM7/TIM6/…（它是子串匹配，
# 'timer' 不是 'TIM1' 的子串，所以匹配不到 —— 这不是大小写问题）

# AI 解析用 JSON
py .../svd_tool.py --chip N32G4FR --reg RCC CR --json
```

`--chip` 省略时自动尝试唯一匹配；多个候选时交互选择。`--svd <路径>` 可显式指定文件。

## 参数表

| 参数 | 必须 | 说明 |
|------|------|------|
| `--detect` | 是（动作） | 列出可用 SVD 文件 |
| `--periph <外设>` | 是（动作） | 列出外设下所有寄存器 |
| `--reg <外设> <寄存器>` | 是（动作） | 寄存器详情（地址/位域/枚举）|
| `--read <外设> <寄存器>` | 是（动作） | 读当前值 + 位域解码 |
| `--find <关键词>` | 是（动作） | 模糊搜索外设/寄存器名 |
| `--svd <file>` | 否 | 显式指定 SVD 文件 |
| `--chip <型号>` | 否 | 按芯片名匹配 SVD（如 N32G4FR）|
| `--backend {jlink,openocd}` | 否 | 读值后端，默认 openocd（无停机）|
| `--ocd-port` | 否 | OpenOCD TCL 端口（默认 6666）|
| `--json` | 否 | 结构化 JSON 输出 |

## 工作流程（AI 执行闭环）

```
1. 找 SVD      --detect 看有哪些芯片；--chip N32G4FR 选中（自动发现 Keil Pack）
2. 查外设      --periph GPIOA → 看有哪些寄存器、基地址
3. 查位域      --reg GPIOA GPIOx_PL_CFG → 哪几位控制什么、枚举值含义
4. 读实时值    --read RCC CR --backend openocd → 免断点读 + 按位域解码
5. 对照排查    把实时值与参考手册/代码意图对照 → 判断配置是否正确、位是否就位
```

示例闭环：`/ea svd --chip N32G4FR --read GPIOA GPIOx_PL_CFG` → 值 0x0000_5555，位域解码 PMODE0[1:0]=01、PMODE7[15:14]=01 → 判断引脚方向配置状态。

## ⚠️ 注意事项

1. **无停机读值是设计目标**：OpenOCD 后端用 `mdw` 直接读内存，**不 halt CPU**，中断/外设持续运行。J-Link 后端会 halt→mem32→go（短暂停 100ms 级），实时性敏感场景用 openocd。
2. **SVD 来源自动发现**：从 Keil Pack 目录（`<Keil>\ARM\PACK`）扫描；找不到时用 `--svd` 显式指定。N32G4FR.svd 在 `Nationstech\N32G4FR_DFP\<ver>\svd\`。
3. **derivedFrom 继承**：SVD 里 `TIM3` 继承 `TIM2`、`GPIOB` 继承 `GPIOA` 等，工具自动解析父级寄存器/位域，无需手动展开。
4. **读值需调试器**：`--read` 需接 ST-Link/DAP/J-Link 且 OpenOCD TCL 端口可达；无硬件时用 `--reg` 看静态定义即可。
5. **访问只读/受保护位**：写 1 清 0 的位（如 `HSEONRDY` 状态位）、只读寄存器读出来无意义，位域枚举表会标明含义。

## 常见错误

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| 未找到匹配 SVD | --chip 拼写不符 | `--detect` 看可用型号；`--svd` 显式指定 |
| 未找到 openocd | 工具未注册/不在 PATH | `/ea setup` 注册，或加入 PATH |
| TCL 连接失败 | OpenOCD 未起/端口被占 | 确认已 `ocd_start` 或端口 6666 可用；`--ocd-port` 指定 |
| 寄存器名不符 | 大小写/别名（如 RCC 的 CR/CR1）| `--find` 模糊搜索确认准确名 |

## 相关文件
- `<SKILL>/tools/svd/scripts/svd_tool.py` - SVD 解析工具
- `commands/debug.md` - 读寄存器/内存的 J-Link/OpenOCD 后端
- `commands/map.md` - 符号/地址映射（函数/变量地址）
