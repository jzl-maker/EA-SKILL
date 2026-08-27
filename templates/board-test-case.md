# 板级测试用例: {{CASE}}

## 基本信息
- 用例ID: {{CASE_ID}}
- 功能: {{FEATURE}}
- 测试类型: 板级工装验收
- 依赖: {{CHIP}} / {{PERIPHERALS}} / {{INTERFACE}}

## 前置条件
- [ ] 板卡上电，串口已连接（{{PORT}}）
- [ ] {{PRE_CONDITION}}

## 测试步骤
| 步骤 | 操作 | 预期 | 判定 |
|------|------|------|------|
| 1 | 上电并复位 | 启动日志出现，`[TEST]` 帧开始 | 观察串口 |
| 2 | {{ACTION}} | {{EXPECTED}} | {{OBSERVE}} |
| 3 | 检查异常/复位 | 无看门狗复位、无 hardfault | RTT/串口 |

## 判定标准
- 全部步骤「预期」满足 → **PASS**
- 任一步骤失败 → 记录实际现象到 HVR，进入 `/ea debug`

## 测试代码
- 板载测试固件: `tests/board/{{CASE}}_test.c`（烧录后运行，经串口输出 `[TEST] PASS/FAIL`）
- 或 host 驱动脚本: `tests/board/{{CASE}}_driver.py`（用 serial/debug 工具自动执行 + 判定）
