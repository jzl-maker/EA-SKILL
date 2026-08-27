# 芯片学习机制

## chips.json 结构

**文件位置**: `~/.claude/chips.json`

```json
{
  "chips": [
    {
      "name": "GD32F407VET6",
      "vendor": "GigaDevice",
      "source": "learned",
      "peripherals": ["GPIO", "UART", "I2C", "SPI", "CAN", "Timer"],
      "lastUsed": "2026-03-27"
    }
  ],
  "builtin": [
    {"name": "STM32F103C8T6", "vendor": "ST", "peripherals": ["GPIO", "UART", "I2C", "SPI", "Timer"]},
    {"name": "STM32F405RGT6", "vendor": "ST", "peripherals": ["GPIO", "UART", "I2C", "SPI", "CAN", "Timer", "ADC"]},
    {"name": "STM32F407VET6", "vendor": "ST", "peripherals": ["GPIO", "UART", "I2C", "SPI", "CAN", "Timer", "ADC", "DAC"]},
    {"name": "GD32F407VET6", "vendor": "GigaDevice", "peripherals": ["GPIO", "UART", "I2C", "SPI", "CAN", "Timer", "ADC"]},
    {"name": "GD32F405RGT6", "vendor": "GigaDevice", "peripherals": ["GPIO", "UART", "I2C", "SPI", "CAN", "Timer", "ADC"]},
    {"name": "CH32V103C8T6", "vendor": "WCH", "peripherals": ["GPIO", "UART", "I2C", "SPI"]},
    {"name": "CH32V203C8T6", "vendor": "WCH", "peripherals": ["GPIO", "UART", "I2C", "SPI", "USB"]}
  ]
}
```

## 芯片识别正则表达式

**识别模式**:

| 文件名模式 | 厂商 | 芯片系列 | 示例 |
|-----------|------|----------|------|
| `system_stm32f4xx.c` | ST | STM32F4xx | STM32F407VET6 |
| `system_stm32f1xx.c` | ST | STM32F1xx | STM32F103C8T6 |
| `system_gd32f4xx.c` | GigaDevice | GD32F4xx | GD32F407VET6 |
| `system_gd32f1xx.c` | GigaDevice | GD32F1xx | GD32F103C8T6 |
| `system_ch32v1xx.c` | WCH | CH32V1xx | CH32V103C8T6 |
| `system_ch32v2xx.c` | WCH | CH32V2xx | CH32V203C8T6 |
| `startup_stm32f103xb.s` | ST | STM32F103 | STM32F103C8T6 |
| `startup_gd32f103xb.s` | GigaDevice | GD32F103 | GD32F103C8T6 |

**正则表达式**:

```regex
# system_xxx.c 文件
system_(stm32f|gd32f|ch32v)(\d+)xx\.c

# startup_xxx.s 文件
startup_(stm32f|gd32f|ch32v)(\d+)xx[b]?\.s
```

## 外设自动检测逻辑

**检测方法**: 在代码中搜索外设初始化函数调用

| 外设 | 检测关键词 | 典型函数 |
|------|-----------|----------|
| GPIO | `gpio_init`, `GPIO_MODE` | `gpio_init(GPIOx, ...)` |
| UART/USART | `usart_init`, `uart_init` | `usart_init(USARTx, ...)` |
| I2C | `i2c_init` | `i2c_init(I2Cx, ...)` |
| SPI | `spi_init` | `spi_init(SPIx, ...)` |
| CAN | `can_init` | `can_init(CANx, ...)` |
| Timer | `timer_init` | `timer_init(TIMERx, ...)` |
| ADC | `adc_init` | `adc_init(ADCx, ...)` |
| DAC | `dac_init` | `dac_init(DACx, ...)` |
| USB | `usb_init` | `usb_init(USBx, ...)` |

**检测流程**:

```
1. 扫描所有 .c 文件
2. 搜索外设关键词
3. 提取_unique_外设列表
4. 添加到芯片信息的 peripherals 字段
```

## 学习触发时机

### 1. 存量接入时
- 检测到新芯片型号，自动添加
- 检测已初始化外设
- 更新到 chips.json

### 2. 项目初始化时
- 用户选择/输入芯片
- 自动学习并更新 chips.json

## 芯片重复处理
- 检测到重复芯片 → 更新 `lastUsed` 时间戳
- 提示用户："芯片已在库中，已更新最近使用时间"

## 芯片选择优先级
1. 已学习的芯片（按最近使用排序）
2. 内置芯片列表
3. 支持自定义输入

## 芯片选择界面（项目初始化）

```
📋 芯片选择

最近使用:
1. GD32F407VET6 (GigaDevice) [已学习]
2. STM32F407VET6 (ST) [已学习]

内置芯片:
3. STM32F103C8T6 (ST)
4. CH32V103C8T6 (WCH)
5. GD32F405RGT6 (GigaDevice)

自定义输入: 输入芯片型号

请选择或输入芯片型号:
```
