# 单片机串口日志参考代码

## 统一日志格式
```c
#define LOG_DBG(fmt, ...)  printf("[DBG] " fmt "\r\n", ##__VA_ARGS__)
#define LOG_ERR(fmt, ...)  printf("[ERR] " fmt "\r\n", ##__VA_ARGS__)
#define LOG_PASS()         printf("[PASS]\r\n")
#define LOG_FAIL()         printf("[FAIL]\r\n")
#define LOG_END()          printf("DEBUG_TEST_END\r\n")
```

## STM32F4xx (GD32F4xx兼容)
```c
// 需要实现 fputc 函数
int fputc(int ch, FILE *f)
{
    usart_data_transmit(USART0, (uint8_t)ch);
    while(RESET == usart_flag_get(USART0, USART_FLAG_TBE));
    return ch;
}

// 使用示例
LOG_DBG("ADC value: %d", adc_value);
LOG_ERR("UART timeout");
LOG_PASS();
LOG_END();
```

## STM32F1xx (GD32F1xx兼容)
```c
// 需要实现 fputc 函数
int fputc(int ch, FILE *f)
{
    USART_SendData(USART1, (uint8_t)ch);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    return ch;
}

// 使用示例同上
```

## CH32V1xx
```c
// 需要实现 fputc 函数
int fputc(int ch, FILE *f)
{
    USART_SendData(USART1, (uint8_t)ch);
    while(USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET);
    return ch;
}

// 使用示例同上
```

## 常用串口配置
| 芯片 | 波特率 | 数据位 | 校验 | 停止位 |
|------|--------|--------|------|--------|
| STM32F4xx | 115200 | 8 | N | 1 |
| STM32F1xx | 115200 | 8 | N | 1 |
| GD32F4xx | 115200 | 8 | N | 1 |
| CH32V1xx | 115200 | 8 | N | 1 |
