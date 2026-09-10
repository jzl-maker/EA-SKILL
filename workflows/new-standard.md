# 工作流: new 标准档（standard）

> 适用：跨模块特性、新外设、需设计但非系统级（默认档位）
> 产出：`brainstorm.md` + `milestones.md`（2 个文件）

## 核心理念（借鉴 superpower）
**brainstorm → plan → execute**：
1. **brainstorm（发散）**：列方案、对比、取舍 → 选定方向
2. **plan（收敛）**：拆 milestones（子步骤） → 锁定路径
3. **execute**：进 verify

标准档内含**嵌入式硬件对齐阶段**（新外设/引脚/时序/实时性约束），保证设计可落板。

## 前置
1. 先读 `<STATE_DIR>/context.md`（芯片/外设/模块地图/保护区）
2. 再读 `<STATE_DIR>/discussion/<YYYYMMDD>-<slug>/requirement.md`（阶段 0 澄清产出）——**需求已坐实，本档不重复做需求分析**
3. 改动遵循 SKILL.md 红线

> 若 `requirement.md` 第 4 节有「未决 / 风险」项，brainstorm 时优先消化，能定则定、不能定则显式标注到 milestones。

## 流程

```
阶段 1: brainstorm — AI 提案 → 用户确认
   ↓
阶段 2: 嵌入式硬件对齐 — 外设/引脚/时序核对 → 用户确认
   ↓
阶段 3: milestones — AI 拆子步骤 → 用户确认
   ↓
阶段 4: 同步状态 → 提示 verify
```

## 阶段 1: brainstorm

**目标**: 用 ~3 个候选方案对比，选定方向。

```markdown
# brainstorm: S<N>-<slug>

## 需求理解
<从 requirement.md 第 5 节引入已确认需求 + 关联 context 中既有模块，不重新分析需求>
<若 requirement.md 第 4 节有未决项，在此列出并给出本档的处理决定>

## 候选方案
### 方案 A: <名字>
- 思路 / 优点 / 缺点·风险 / 预估工作量

### 方案 B: <名字>（同上）

### 方案 C: <名字>（若有）

## 推荐
**方案 <X>**，理由：<一句话>

## 关键技术点
- <技术点>
```

提示：`继续` 采用 / `A`/`B`/`C` 改选 / `补充: <...>` 添加约束 / `取消`
保存：`<STATE_DIR>/discussion/<YYYYMMDD>-<slug>/brainstorm.md`

## 阶段 2: 嵌入式硬件对齐（本档嵌入式特色）

选定方案后，核对硬件维度（写回 brainstorm.md 的「硬件对齐」区）：

```markdown
## 硬件对齐
| 项 | 现状（context） | 本方案需求 | 冲突? |
|----|----------------|-----------|-------|
| 引脚占用 | PA0=KEY0 ... | 新功能用 PB3 | ⚠️ 与 SWD 复用 |
| 外设 | UART2 空闲 | 需 UART2 + 中断 | 可 |
| 时钟/时序 | 72MHz 主频 | 需 1ms 时基 | 现有 Timer 可复用 |
| 中断 | 已用: TIM3, UART1 | 新增 UART2 中断 | 向量表需改 → 保护区! |

结论: <可行 / 需调整方案 / 需改保护区文件（要 /ea approve）>
```

- 引脚冲突、时钟不足、外设占用 → 提前暴露，避免编码后返工
- 若涉及保护区文件（中断向量表/链接脚本）→ 明确标记，改动需 `/ea approve`

## 阶段 3: milestones

```markdown
# milestones: S<N>-<slug>

## 选定方案
<复述 + 关键决策>

## 子步骤
### S<N>-A: <名字>
- 内容 / 依赖 / 验证 / 预估
### S<N>-B: ...（≤ 5 个子步骤）
```

**约束**：子步骤 ≤ 5；每个子步骤必须可独立 `/ea verify`。

提示：`继续` / `合并 A B` / `拆分 A` / 直接编辑
保存：`milestones.md`

## 阶段 4: 同步状态

1. `state.md`：当前步骤 = `S<N>-A`，下一步 = `/ea verify s<N>-a`
2. `project-spec.md` 追加步骤行
3. `decisions.md` 追加选定方案的关键决策

## 收尾输出

```
🎉 S<N> 计划完成（标准档）
📄 brainstorm + milestones
🎯 选定方案: <名字>   📦 子步骤数: <N>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
首子步骤: S<N>-A — <名字>
下一步: 开始编码 S<N>-A → 完成后 /ea verify s<N>-a
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## 降档触发

brainstorm 时发现「这就是个 bugfix / 单文件调参」→ 降轻档（`--light`），保留 brainstorm.md 作参考。

## 相关文件
- `commands/new.md` — 入口
- `workflows/req-clarify.md` — 阶段 0 需求澄清（上游）
- `templates/requirement.md` — 已确认需求（先读）
- `templates/context.md` — 工程上下文（硬件对齐数据源）
- `commands/verify.md` — 验证入口
