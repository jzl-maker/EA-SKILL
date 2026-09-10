# 工作流: new 轻档（quick）

> 适用：< 2h 工作量、单文件改动、bugfix、参数调优、小特性
> 产出：1 个 `quick-plan.md`，立即可执行

## 核心理念
**最小可执行计划**：不发散、不头脑风暴，只确认目标和边界 → 直接 verify。

## 前置
1. 先读 `<STATE_DIR>/context.md`（工程上下文）
2. 再读 `<STATE_DIR>/discussion/<YYYYMMDD>-<slug>/requirement.md`（阶段 0 澄清产出）——**已确认的需求直接引用，不重复分析**
3. 改动遵循 SKILL.md 红线（保护区禁随意改、增量修改、改动给 diff）

> 轻档的澄清深度通常为「快」（≤3 题），`requirement.md` 可能很短，属正常。

## 流程

```
1. AI 输出 quick-plan 草稿（基于功能描述 + context）
   ↓
2. 用户「继续」或修改
   ↓
3. 保存 quick-plan.md
   ↓
4. 更新 state.md / project-spec.md
   ↓
5. 提示 /ea verify s<N> 进入验证
```

## 步骤 1: AI 输出 quick-plan 草稿

```markdown
# quick-plan: S<N>-<slug>

## 一句话目标
<10-30 字以内>

## 需求来源
<requirement.md 路径>（阶段 0 已确认，勿重复分析）

## 改动清单（≤ 5 项）
- [ ] 文件1 - 改动摘要（涉保护区？无）
- [ ] 文件2 - 改动摘要

## 验证方式
<跑测试 / 编译烧录看现象 / 用户口述>

## 不做的事（边界）
- 不重构无关代码、不引入新依赖
```

输出后提示：
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 输入 `继续` 采用此计划
✏️  或直接修改任意字段
🔄 输入 `升档 标准` 切换档位
❌ 输入 `取消` 退出
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## 步骤 2: 保存
- 路径：`<STATE_DIR>/discussion/<YYYYMMDD>-<slug>/quick-plan.md`

## 步骤 3: 同步状态
- `state.md`：当前步骤 = S<N>（开发中），下一步 = `/ea verify s<N>`
- `project-spec.md` 步骤表追加一行

## 步骤 4: 收尾输出

```
🎉 S<N> 计划完成（轻档）
📄 计划文件: <STATE_DIR>/discussion/<...>/quick-plan.md
📝 改动清单: <N> 项
✅ 验证方式: <一句话>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
下一步:
  开始编码 → 完成后 /ea verify s<N>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## 边界 / 升档触发

轻档中发现：
- 改动 > 5 个文件 / 跨多子系统
- **涉及新硬件外设接入**（GPIO/UART/I2C/SPI/Timer 新配置、引脚冲突）

→ AI 主动提示：「此需求可能超出轻档范围，建议升档为标准档（`/ea new ... --std`）」

## 相关文件
- `commands/new.md` — 入口
- `workflows/req-clarify.md` — 阶段 0 需求澄清（上游）
- `templates/requirement.md` — 需求产出（先读）
- `templates/context.md` — 工程上下文（先读）
