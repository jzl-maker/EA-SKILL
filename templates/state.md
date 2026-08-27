# state.md — 项目当前状态（rec 默认只读这个）

> ⚡ 目标：≤ 50 行，让 `/ea rec` 只加载这一个文件就能恢复项目上下文。
> 详细工程上下文、历史、决策、会话日志按需用 `/ea stat -v` 查询。

## Meta
- **项目**: <项目名>
- **类型**: embedded
- **当前步骤**: S<N>(-<sub>)?  状态  emoji
- **更新时间**: YYYY-MM-DD
- **会话**: sess-YYYYMMDD-NNN

## 下一步动作
1. <最具体的下一动作，可直接执行>
2. <第二个候选>

## 最近 3 条关键决策
- [YYYY-MM-DD] <决策摘要，一行>
- [YYYY-MM-DD] <决策摘要，一行>
- [YYYY-MM-DD] <决策摘要，一行>

## 阻塞项 / 待办
- [ ] <阻塞或待办，一行>

## 详细资料指针
| 内容 | 文件 |
|------|------|
| 工程上下文 | `context.md`（芯片/外设/保护区/基线） |
| 步骤全表 | `project-spec.md` |
| 会话历史 | `sessions/<id>.md` |
| 决策全集 | `decisions.md` |
| 问题追踪 | `problem-log.md` |
| HVR 记录 | `checkpoints/` |
| 修改记录 | `records/<step>-<date>.md` |

<!-- state.md 由 init/new/verify/record 命令自动维护；用户也可直接编辑 -->
