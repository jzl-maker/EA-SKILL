# 工作流: context-build（工程上下文构建）

> 由 `/ea si`（存量接入）主流程调用，`/ea init` 生成骨架。
> 目的：让 AI 对工程有「上下文感知」——芯片/外设/模块地图/既有约定/保护区，动手前先懂工程。

## 输出目标

写入 `<STATE_DIR>/context.md`，结构如下（模板 `templates/context.md`）：

```markdown
# 工程上下文：<项目名>

<!-- summary:begin -->
## 芯片
- 型号/厂商/Flash/RAM
- 外设列表（GPIO/UART/I2C/SPI/CAN/Timer/ADC/DAC/USB...）

## 工程与工具链
- 工程类型（Keil/CubeMX/ESP-IDF/PlatformIO）
- 构建入口（.uvprojx 路径 / sdkconfig / platformio.ini）
- Keil target、链接脚本（.sct/.ld/.icf）
- 工具链：UV4 / OpenOCD(interface) / J-Link(device)

## 模块地图
| 模块 | 文件 | 职责 | 依赖 |
|------|------|------|------|
| main | app/main.c | 主循环调度 | ... |
| 驱动层 | drv/*.c | UART/GPIO/Timer | ... |
| 应用层 | task/*.c | 业务逻辑 | ... |

- printf 重定向到: <串口N>
- 调试接口: <LED/按键/串口>

## 既有约定
- 编码: GB2312（.c/.h）⚠️
- 命名: <HAL_xxx / drv_xxx ...>
- 注意事项: <已知坑，随 record 增量补充>

## 保护区清单
| 文件 | 原因 |
|------|------|
| startup_<chip>.s | 启动/中断向量表 |
| system_<chip>.c | 核心初始化 |
| <project>.sct | 链接脚本 |

<!-- summary:end -->

## 关键文件基线（project_guard --snapshot 生成）
- `startup_<chip>.s` sha256: <hex>
- `app/main.c` sha256: <hex>

## 增量学习记录

- [YYYY-MM-DD] 新增模块 X / 新约定 Y / 新坑 Z
```

## 构建步骤

1. **芯片识别**：正则扫描 `system_*.c` / `startup_*.s`
   - `system_(stm32f|gd32f|ch32v)(\d+)xx\.c` → 芯片系列
   - 更新 `~/.claude/chips.json`（复用 `workflows/chip-learning.md`）
2. **外设识别**：全 `.c` 文件搜初始化函数关键词
   - `gpio_init` / `usart_init` / `i2c_init` / `spi_init` / `can_init` / `timer_init` / `adc_init` / `dac_init` / `usb_init`
3. **工程类型**：探测 `*.uvprojx`(Keil) / `*.ioc`(CubeMX) / `sdkconfig`(ESP-IDF) / `platformio.ini`(PlatformIO)
4. **模块地图**：分析目录结构与入口
   - main 函数位置、驱动层/应用层目录、中断处理文件
   - printf 重定向（`fputc`/`_write`/`USART_SendData`）
   - 调试接口（LED GPIO、按键 GPIO、调试串口）
5. **既有约定**：编码检测（GB2312 vs UTF-8 采样）、文件命名、宏定义风格
6. **保护区清单**：登记 `startup_*.s` / `system_*.c` / 链接脚本 / 向量表文件
7. **基线快照**：`python <SKILL>/tools/shared/project_guard.py --snapshot` → 哈希写入 context
8. **写 context.md** 到 `<STATE_DIR>/context.md`

   ⚠️ **必须保留 `<!-- summary:begin -->` / `<!-- summary:end -->` 标记**（包住
   芯片 / 工程与工具链 / 保护区清单）。`/ea rec` 靠它只读这一段做轻量恢复 ——
   标记丢了，rec 就得整读整个文件（实测有工程达 78 KB ≈ 20K tokens）。
   也**不要**把会无限增长的区段（基线 sha256、增量学习记录）搬进标记内。

## 设计原则
- ✅ 只读分析，零修改源文件（老工程保护）
- ✅ 基线一旦建立，后续所有改动可 diff、保护区可审计
- ✅ 增量学习：`/ea record` 每次回写「增量学习记录」区段，context 越用越准
- ✅ **轻量恢复**：摘要区段（summary 标记内）供 `/ea rec` 用；其余区段按需读

## 相关文件
- `templates/context.md` — 模板
- `workflows/chip-learning.md` — 芯片识别/学习细则
- `tools/shared/project_guard.py` — 基线快照
- `commands/si.md` / `commands/init.md` — 入口命令
