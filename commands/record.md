# 命令: /ea record (修改记录)

## 功能
开发/调试完成后，输出该步骤的**修改记录**：改动文件清单 + diff 摘要 + 测试/验证证据 + 遗留问题。同时回写 `context.md`（增量学习：新模块/新约定）。

## 触发
```
/ea record s<编号>    # 如 /ea record s7
```

## 执行流程

1. **【状态目录】** `get_state_dir()` → `<STATE_DIR>`
2. **【改动清单】** 用 git 或基线快照计算本次改动：
   - git 项目：`git status` + `git diff --stat`
   - 非 git 项目：`python ~/.claude/skills/ea-skill/tools/shared/project_guard.py --check` 基线比对
3. **【保护区核对】** 改动清单中是否有保护区文件：
   - 有且无 approve 记录 → 告警，列出 diff 待用户确认
   - 有且有 approve 记录 → 附上审批条目
4. **【测试证据】** 收集本步骤测试结果（单测 PASS/FAIL、板级用例、编译/烧录/J-Link/串口记录）
5. **【调试证据】** 如本步骤有调试过程，附 debug 的 PC/变量/寄存器现场 → 根因结论
6. **【写记录】** 生成 `<STATE_DIR>/records/<step>-<YYYYMMDD>.md`
7. **【回写 context】** 增量更新 `context.md`：新增模块/新约定/新增保护区文件
8. **【输出摘要 + 提议 commit】**

## 记录格式

```markdown
# 修改记录 S<N> — <步骤名>

日期: YYYY-MM-DD   会话: sess-<id>

## 一、改动文件清单
| 文件 | 性质 | 说明 | 涉保护区 |
|------|------|------|----------|
| src/app/task_display.c | 修改 | 新增翻转逻辑 | 否 |
| src/drv/drv_uart.c | 修改 | 调整波特率配置 | 否 |

## 二、Diff 摘要
<关键 diff 片段，或指向 git diff / project_guard --check 输出>

## 三、测试结果
| 类型 | 结果 |
|------|------|
| 单元测试 | ✅ 5/5 PASS |
| 板级用例 | 3/4 PASS（1 待复查）|
| 编译 | ✅ 错误:0 警告:2 |
| 烧录 | ✅ verified |
| J-Link 验证 | ✅ PC=0x08000xxx 进入 main，RTT 有启动日志 |
| 串口 | ✅ 启动日志正常 |

## 四、调试证据（如有）
- 现象: LED 不翻转
- 断点: task_display() PC=0x08001d28
- 根因: 定时器计数未使能 → 修复 drv_timer
- 验证: 上电后 LED 正常翻转

## 五、遗留问题 & 下一步
- [ ] P1: 板级用例 #4 在低温下待复查
- 下一步: /ea verify s<N+1> 或 /ea new <下一功能>
```

## 增量学习（context 回写）

record 完成后更新 `context.md`：
- 新增/修改模块 → 模块地图更新
- 新踩的坑 → 既有约定「注意事项」
- 新加的文件在保护区 → 保护区清单补充

## 设计原则
- ✅ 每次开发/调试留痕：文件 + diff + 证据齐全
- ✅ 保护区审计闭环：改动有据可查
- ✅ 工程越用越懂：context 随 record 增量学习

## 相关文件
- `tools/shared/project_guard.py` — 改动清单/保护区审计
- `commands/verify.md` — 验证（record 通常在 verify 后）
- `templates/context.md` — 工程上下文（增量回写目标）
- `commands/result` 旧命令已被本命令替代
