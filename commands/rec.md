# 命令: /ea rec (恢复项目)

## 功能
最小代价加载项目当前状态。只读 `state.md`（≤50 行）+ `context.md` 摘要，按需查询详情。

## 触发
```
/ea rec [项目名称|路径]
```

## 加载策略（瘦身设计）

| 加载层 | 文件 | 何时读 |
|--------|------|--------|
| L0 默认 | `<STATE_DIR>/state.md` | 总是（≤50 行）|
| L1 类型 | `<STATE_DIR>/project.json` | 总是（< 20 行）|
| L2 上下文 | `<STATE_DIR>/context.md` | 总是（摘要：芯片/模块/保护区）|
| L3 详情 | `project-spec.md` / `problem-log.md` / `sessions/<id>.md` | 按需（用户问到才读）|

## 执行流程

1. **【状态目录】** `get_state_dir()`：`.ea/` 优先，回退 `.em/`（老项目兼容）
2. **【最小加载】** 读 `state.md` + `project.json` + `context.md` 摘要
3. **【上下文摘要】** 从 context.md 输出一行关键信息（芯片/当前焦点/保护区警告）
4. **【输出摘要 + 下一步】**

## 摘要输出格式

```
📂 项目恢复完成 — <项目名>  (embedded)
芯片: GD32F407VET6 | 焦点: 显示模块
当前步骤: S3 — 验证中
下一步:   <state.md 第 1 条 next-action>
详情命令: /ea stat -v
```

## 设计原则
- ❌ 不再一次性灌入 memory-log/project-spec/problem-log 全文
- ✅ rec 只回答「我现在该做什么」+ 工程上下文要点，详情按需查

## 相关文件
- `templates/state.md` — state 模板
- `templates/context.md` — 工程上下文模板
- `commands/stat.md` — 详细状态查询（`-v` 全景）
