# 项目规格单：{{PROJECT_NAME}}

## Meta
- **创建日期**: {{CREATE_DATE}}
- **项目类型**: embedded
- **当前步骤**: {{CURRENT_STEP}}
- **整体状态**: {{PROJECT_STATUS}}
- **项目路径**: {{PROJECT_PATH}}
- **芯片**: {{CHIP}}
- **工具链**: {{TOOLCHAIN}} / 调试接口: {{INTERFACE}}

## 开发步骤状态

| 步骤 | 名称 | 状态 | 日期 | 验证方式 |
|------|------|------|------|----------|
| S1 | {{S1_NAME}} | {{S1_STATUS}} | {{S1_DATE}} | 编译→烧录→J-Link→串口 |

## 测试记录

| 类型 | 用例/代码 | 结果 |
|------|-----------|------|
| 单元 | `tests/unit/<name>.md` + `.c` | PASS/FAIL |
| 板级 | `tests/board/<case>.md` + 固件 | PASS/FAIL |

## 保护区改动

| 文件 | 改动摘要 | 审批 |
|------|----------|------|
| （无） | | |

## 问题追踪

### 待解决问题
（无）

### 已解决问题
（无）

## 参考文档
- 芯片手册: {{DATASHEET_URL}}
- 开发板原理图: {{SCHEMATIC_URL}}
