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
| L2 上下文 | `<STATE_DIR>/context.md` | 总是，但**只读摘要区段**（见下）|
| L3 详情 | `project-spec.md` / `problem-log.md` / `sessions/<id>.md` / **context.md 其余区段** | 按需（用户问到才读）|

### ⚠️ L2 不要整读 context.md

`context.md` **会随工程增长到几十 KB**（实测某工程 77,832 B ≈ 20K tokens）—— 其中
「关键文件基线」的 sha256 列表和「增量学习记录」随每次 `/ea record` 变长。
整读等于把一次"轻量恢复"变成灌 20K tokens，与 `≤50 行 / 轻量恢复` 的设计直接矛盾。

**正确读法**：只读 `<!-- summary:begin -->` 与 `<!-- summary:end -->` 之间
（芯片 / 工程与工具链 / 保护区清单）：

```
Grep: pattern="<!-- summary:(begin|end) -->" path="<STATE_DIR>/context.md" -n
Read: offset=<begin 行> limit=<end 行 - begin 行 + 1>
```

**老工程没有标记时**：按标题定位，只读 `## 芯片` 到 `## 保护区清单` 结束这一段，
**不要**因为找不到标记就退回整文件读取。其余区段等用户问到再按需读。

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
