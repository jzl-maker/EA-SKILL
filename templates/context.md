# 工程上下文：<项目名>

> 由 `/ea si`（存量）或 `/ea init`（新建）生成；`/ea record` 增量更新。
> ⚠️ 动手改代码前必读本文件；改动遵循 SKILL.md 红线（保护区禁改 / 增量修改 / 改动给 diff）。
>
> 🔻 `<!-- summary:begin -->` / `<!-- summary:end -->` 之间是**轻量恢复区段**：
> `/ea rec` 只读这一段。**标记不可删**，也不要把会无限增长的区段（基线 sha256 列表、
> 增量学习记录）搬进标记内 —— 那会把 rec 变回全文加载。

<!-- summary:begin -->
## 芯片
- 型号: <如 STM32F103C8T6> / 厂商: <ST>
- Flash: <64KB> / RAM: <20KB>
- 外设列表: <GPIO/UART/I2C/SPI/CAN/Timer/ADC/DAC/USB 已占用情况>

## 工程与工具链
- 工程类型: Keil / CubeMX / ESP-IDF / PlatformIO
- 构建入口: <相对路径 .uvprojx / sdkconfig / platformio.ini>
- Keil target: <Target1>
- 链接脚本: <*.sct / *.ld / *.icf>
- 工具链: UV4=<path> / OpenOCD(interface=<stlink|jlink|cmsis-dap>) / J-Link(device=<device>)
- 调试接口: <SWD 引脚占用情况>

## 模块地图
| 模块 | 文件 | 职责 | 依赖 |
|------|------|------|------|
| main | app/main.c | 主循环调度 | ... |
| 驱动层 | drv/*.c | UART/GPIO/Timer | ... |
| 应用层 | task/*.c | 业务逻辑 | ... |

- printf 重定向到: <串口N>（fputc/_write 实现位置）
- 调试接口: <LED GPIO / 按键 GPIO / 调试串口>

## 既有约定
- 编码: GB2312（.c/.h）⚠️ 禁止用 UTF-8 编辑器改中文
- 命名: <HAL_xxx / drv_xxx ...>
- 注意事项: <已知坑，随 record 增量补充>

## 保护区清单
| 文件 | 原因 |
|------|------|
| startup_<chip>.s | 启动/中断向量表 |
| system_<chip>.c | 核心初始化 |
| <project>.sct | 链接脚本 |

<!-- summary:end -->

## 关键文件基线（project_guard）
> 由 `tools/shared/project_guard.py --snapshot` 生成，`--check` 比对。
- `startup_<chip>.s` sha256: <hex>
- `system_<chip>.c` sha256: <hex>
- `app/main.c` sha256: <hex>

## 增量学习记录
- [YYYY-MM-DD] 新增模块 X / 新约定 Y / 新坑 Z
