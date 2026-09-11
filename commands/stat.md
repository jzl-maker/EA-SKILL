# 命令: /ea stat (项目状态)

## 功能
查看项目状态：默认极简（state.md），`-v` 详细（步骤全表、最近会话、决策、问题、保护区审计）。

## 触发
```
/ea stat              # 默认：读 state.md，输出 ≤10 行
/ea stat -v           # 详细：project-spec/最近会话/decisions/problem-log/context
/ea stat steps        # 只显示开发步骤状态表
/ea stat next         # 只显示下一步动作
```

## 加载策略

| 模式 | 加载文件 | 用途 |
|------|---------|------|
| 默认 | `state.md` + `project.json` | 快速看现在做啥 |
| `-v` | + `project-spec.md` + context.md + `decisions.md` + `problem-log.md` | 全景 |
| `steps` | `project-spec.md` 的步骤表区段 | 看进度 |
| `next` | `state.md` 的「下一步动作」区段 | 极简 |

## 执行流程
1. **【状态目录】** `get_state_dir()` → `<STATE_DIR>`
2. **按模式加载**对应文件
3. **【步骤号一致性校验】**（默认与 `-v` 模式都做）三处步骤号必须一致：

   | 来源 | 取法 |
   |------|------|
   | `state.md` | Meta 区 `- **当前步骤**: S<N>` |
   | `project-spec.md` | Meta 区 `- **当前步骤**: S<N>` |
   | `project-spec.md` 步骤表 | 「开发步骤状态」表里**编号最大的 S 行**，并检查 `S<N>` 那一行是否存在 |

   不一致时**必须显式告警**，不要默默按其中一个显示。这是纯粹的一致性比对，
   不靠人工发现 —— 否则 AI 每轮都要重新撞一次这个矛盾。
4. **输出对应格式**

## 输出格式

### 默认模式
```
📍 <项目名> — S<N> <状态>  (embedded)
更新: YYYY-MM-DD   会话: sess-...

下一步:
1. <动作1>

详情: /ea stat -v
```

步骤号不一致时，紧接着补一行（**只在真不一致时出现**，一致就不占地方）：
```
⚠️ 步骤号不一致: state.md=S8 / project-spec.md 当前步骤=S7 / 步骤表最大=S7
   → S8 在步骤表里还没有行，建议先补 project-spec.md 再继续
```

### `-v` 模式
```
📍 <项目名> (embedded) — S<N> <状态>
芯片: <chip> | 工具链: <uv4+openocd+jlink>

━━ 步骤状态 ━━
| S1 | ✅ | 板级驱动 |
| S2 | 🔄 | <当前> |

━━ 一致性 ━━
state.md=S<N> | spec 当前步骤=S<N> | 步骤表最大=S<N>   ✅ 一致
（不一致时改为：⚠️ <各处取值> + 缺哪一行 / 该补什么）

━━ 工程上下文 ━━
<芯片/外设/模块地图/保护区摘要>

━━ 待解决问题 ━━
<P0/P1 列表>

下一步:
- ...
```

## 设计原则
- ✅ 默认极简 → 用户主动 `-v` 才详细（渐进式查询）
- ✅ 嵌入式：`-v` 并入 context 摘要与保护区提醒

## 相关文件
- `commands/rec.md` — 恢复，加载同样的 state.md
- `templates/state.md` — state 模板
- `templates/context.md` — 工程上下文模板
