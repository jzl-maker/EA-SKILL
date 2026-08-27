# tools/svd — SVD 寄存器解析

解析 CMSIS-SVD 寄存器描述文件（Keil Pack 自带），提供「外设 → 寄存器 → 位域 → 枚举值」四层查询，并可免断点读寄存器当前值。

## 文件

| 文件 | 说明 |
|------|------|
| `scripts/svd_tool.py` | CLI：--detect / --periph / --reg / --read / --find |

## 用法

```bash
py scripts/svd_tool.py --detect                          # 发现可用 SVD
py scripts/svd_tool.py --chip N32G4FR --periph GPIOA     # 外设寄存器清单
py scripts/svd_tool.py --chip N32G4FR --reg GPIOA MODER  # 位域/枚举
py scripts/svd_tool.py --chip N32G4FR --read RCC CR      # 读当前值 + 位域解码
py scripts/svd_tool.py --chip N32G4FR --find timer       # 模糊搜索
```

## 实现要点

- **SVD 自动发现**：`find_svd_files()` 从 tool_config 的 uv4 路径推导 Keil Pack 目录（`Path(uv4).parents[1]/ARM/PACK` + `%LOCALAPPDATA%/Arm/Packs`）递归扫描 `*.svd`。
- **derivedFrom 继承**：两遍解析，peripheral 级 + register 级继承（`TIM3`←`TIM2`、`GPIOB`←`GPIOA`），register 用递归 + 缓存。
- **读当前值**：`--backend openocd`（默认）用 TCL `mdw` 运行中直读，**不 halt CPU**（无停机监控）；`--backend jlink` 用 J-Link Commander halt+mem32+go。
- **纯标准库**：xml.etree/dataclasses/socket，无第三方依赖。

## 数据模型

```
SvdPeripheral(base, registers[..])
  └─ SvdRegister(offset, size, access, fields[..])
       └─ SvdField(bit_offset, bit_width, enum{value: name})
```

## 依赖

- `--read` 需 OpenOCD（TCL 端口 6666）或 J-Link 工具链；`--detect`/`--periph`/`--reg`/`--find` 纯静态解析，无需硬件。
