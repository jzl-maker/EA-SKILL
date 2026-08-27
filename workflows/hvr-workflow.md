# HVR 工作流细则（人-AI 协作版）

## 核心定位

**HVR（人工验证请求）是 AI 维护、人类确认的工作区。**

- **人类只做**：执行验证操作、口头描述观察现象
- **AI 负责**：记录到 HVR 文件、执行四连（编译→烧录→J-Link 验证→串口）、分析根因、给建议

## 核心流程

```
1. 用户: /ea verify s<N>
   AI: 状态 🚧开发中→🔄验证中
   AI: 生成 HVR 文件（templates/hvr-template.md）
   AI: 执行四连（verify-flow.md）→ 记录到 HVR「嵌入式执行记录」
   AI: 跑 project_guard --check → 保护区/增量审计
   AI: 输出验证清单 → 提议 commit
       ↓
2. 用户: 执行物理验证 → 观察现象 → 口述给 AI
   AI: 更新 HVR「实际结果」→ 判定
       ↓
   【通过】            【失败】
   ✅ 状态→完成        🔁 状态→返工中
   推进下一步           AI 分析根因 → 用 /ea debug 定位
   提示下一步           → 用户确认 → 修复 → 重新 verify
```

## 两种情况

### 情况 A：验证通过
```
/ea verify s<N> 完成 → 用户口述现象符合预期
AI: HVR 结论 ✅ 通过 → state.md 推进下一步 → 建议 /ea record s<N> 输出修改记录
```

### 情况 B：验证失败
```
用户口述现象不符 → HVR 结论 ❌ 失败
AI: 状态 🔁返工中 → 分析日志 → 用 /ea debug（RTT/断点/内存/寄存器）定位根因
AI: 给出「现象→根因→修复方案」→ 用户确认 → 增量修改（给 diff）
用户: /ea verify s<N> 重新验证
```

## 失败处理要点

1. 失败先**更新 HVR**（最重要），再创建 problem-log 问题记录
2. 讨论模板：失败现象 / 相关 HVR / 请用户描述观察
3. 根因分析给「最可能原因 + 排查方向」，用户确认后记录「解决方案」
4. 问题解决后：problem-log 状态 → resolved，写入闭环记录

## 关键提醒

⚠️ 失败时必须按顺序执行：
1. 先更新 HVR 文件
2. 再写 problem-log.md
3. 用 `/ea debug` 定位根因（RTT/断点/内存/寄存器证据链）
4. 修改前给 diff，涉及保护区文件必须 `/ea approve`

## 相关文件
- `templates/hvr-template.md` — HVR 模板（含嵌入式四连执行记录表）
- `workflows/verify-flow.md` — 四连执行细则
- `commands/verify.md` — verify 入口
- `commands/record.md` — 验证通过后的修改记录
- `commands/debug.md` — 失败定位
