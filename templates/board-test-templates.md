# 板级工装测试用例模板库

> `/ea test board <功能>` 按功能套用以下模板生成 `tests/board/<case>.md` + 板载测试固件/驱动脚本。
> 每个用例统一输出 `[TEST] <CASE_ID> PASS/FAIL` 帧，供 AI 用 serial/debug 工具自动判定。

## 1. 上电自检（POWER_ON）

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 上电并复位 | 串口输出 `[BOOT]` 启动日志 |
| 2 | 等待 2s | 无看门狗复位，`[TEST] POWER_ON PASS` |

固件: 进 main 后延时 2s，无 hardfault → 输出 PASS。

## 2. LED 指示（LED）

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 触发功能启动 | LED 按约定频率闪烁 |
| 2 | 观察 5s | 频率稳定无抖动，`[TEST] LED PASS` |

固件: 翻转 GPIO，用户观察，AI 配合记录频率。

## 3. 按键响应（KEY）

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 按下按键 | 串口输出 `[KEY] pressed` |
| 2 | 松开按键 | `[KEY] released`，逻辑正确响应 |
| 3 | 长按 3s | 触发长按事件，`[TEST] KEY PASS` |

固件: 中断/轮询检测，输出事件帧。

## 4. 串口环回（UART_LOOPBACK）

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 向串口发送测试串 | 回显相同内容 |
| 2 | 校验字节数 | 无丢字节，`[TEST] UART_LOOPBACK PASS` |

驱动: host 脚本发送→接收→比对。

## 5. 外设响应（PERIPHERAL，I2C/SPI/ADC...）

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 触发外设读写 | 读回值在预期范围 |
| 2 | 重复 10 次 | 稳定无超时，`[TEST] PERIPHERAL PASS` |

固件: 循环读写外设寄存器，异常输出 `[ERR]`。

## 6. 异常复位恢复（RESET_RECOVERY）

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 触发异常（如非法指针） | 复位后正常启动 |
| 2 | 输出复位原因 | `RCC_CSR` 复位标志有效，`[TEST] RESET_RECOVERY PASS` |

固件: 上电读复位标志，判断 hardfault/watchdog 复位并上报。

## 7. 数据一致性（DATA_INTEGRITY，Flash/EEPROM）

| 步骤 | 操作 | 预期 |
|------|------|------|
| 1 | 写入测试数据 | 写返回成功 |
| 2 | 复位后读回 | 数据一致，`[TEST] DATA_INTEGRITY PASS` |

固件: 写入→延时→读回比对，不一致输出 `[FAIL]`。

---

## 板载测试固件骨架

```c
/* tests/board/<case>_test.c — 烧录后运行，串口输出 [TEST] 判定帧 */
#include <stdio.h>
/* #include "app.h" */

int test_<case>_entry(void)
{
    printf("[TEST] <CASE_ID> start\r\n");
    /* TODO: 调用被测逻辑，按结果输出 */
    /* if (run_feature() == 0) { printf("[TEST] <CASE_ID> PASS\r\n"); } */
    /* else { printf("[TEST] <CASE_ID> FAIL\r\n"); } */
    printf("[TEST] <CASE_ID> FAIL (not implemented)\r\n");
    return 1;
}
```

## host 驱动脚本骨架

```python
# tests/board/<case>_driver.py — host 侧自动执行 + 判定
# 用 serial-monitor / jlink-debug 工具驱动板卡，抓取 [TEST] 帧判定 PASS/FAIL
# AI 生成：按用例步骤调用 flash → 复位 → 读串口 → 断言
```
