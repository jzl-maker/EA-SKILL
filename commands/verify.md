# 命令: /ea verify (步骤验证)

## 功能
对某个开发步骤做验证：**编译 → 烧录 → J-Link 运行验证 → 串口日志** 四连 + HVR 记录 + 保护区/增量审计 + commit 提议。

## 触发
```
/ea verify s<编号>    # 如 /ea verify s7
```

步骤参数使用 `/ea new` 分配的 S 编号。

## 执行流程

1. **【状态目录】** `get_state_dir()` → `<STATE_DIR>`
2. **【读 context】** 读取 `<STATE_DIR>/context.md`（芯片/外设/构建入口/保护区基线）
3. **【更新状态】** `state.md`: 当前步骤 → `S<N>` 🔄 验证中；`project-spec.md` 步骤表同步
4. **【四连执行】** 加载 `workflows/verify-flow.md`：
   - **编译** `build-keil` → 成功自动进烧录；失败分析编译日志请求修复
   - **烧录** 后端**可插拔**：ST-Link/CMSIS-DAP → `openocd`；J-Link → `--backend jlink-native`
     （Windows 装了 SEGGER 驱动时 OpenOCD 用不了 J-Link，判据 `LIBUSB_ERROR_NOT_SUPPORTED`）
     → 成功自动进 J-Link 验证
   - **J-Link 运行验证** `jlink-debug`：`--regs` 读 PC/SP 确认固件进入 main；`--rtt` 抓启动日志；必要时 `--mem` 读固件区内存
   - **串口** `serial-monitor` 抓业务启动日志；**无串口时降级为 RTT** 并在 HVR 标注
   - 结果记入 HVR 的「执行记录」区段

   ⚠️ **任一环因环境不可达时，先走降级路径，不要直接判"验证失败"** —— 那报的是
   "环境没配好"，却看起来像"代码有问题"。降级事实与**未被覆盖的验证项**都要写进 HVR。
5. **【保护区/增量审计】** `python <SKILL>/tools/shared/project_guard.py --check`
   - 与 context.md 基线比对：保护区文件有改动且无 approve 记录 → **告警并阻塞验证**
   - 输出改动清单 + **保护区 diff**（供用户确认；关键源文件的 diff 加 `--diff`）
6. **【git 可用性前置检查】**（要用 git 出 diff 时**必须先跑**）
   ```bash
   python <SKILL>/tools/shared/git_state.py --root <工程目录>
   ```
   - 退出 0 → `git diff` 可用，继续按 git 流程
   - 退出 1/2 → **不要**再拿 `git diff --stat` 当改动清单：它会返回空输出，
     看起来像"没有改动"。改用上一步的 `project_guard --check --diff` 基线比对

   ⚠️ 两种静默失效要认得：① 仓库**还没有任何提交** → `git diff` 恒空；
   ② 仓库根**不是**工程目录（工程是某仓库的子目录）→ 在仓库根 `git add .`
   会把同级工程一起提交。`git_state.py` 会把这两种情况直接说破。
7. **【生成 HVR】** `<STATE_DIR>/checkpoints/HVR-<步骤>-<序号>.md`（模板 `templates/hvr-template.md`，嵌入式四连字段内嵌）
8. **【输出验证清单】** 待用户口述物理现象的检查点（LED/按键/波形等）
9. **【提议 commit】** 见下

## commit 提议

```
━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 提议 commit
建议 commit message:
  [S<step>] feat: verify <步骤描述> with HVR-<序号>

待提交文件:
  M  <STATE_DIR>/checkpoints/HVR-<步骤>-<序号>.md
  M  <STATE_DIR>/state.md
  M  <STATE_DIR>/project-spec.md
  M  <本次实际改动的源文件，来自 diff>

确认提交？[y/n/edit]
━━━━━━━━━━━━━━━━━━━━━━━━━━
```

| 输入 | 行为 |
|------|------|
| `y` / `确认` | `git add` + `git commit -m "..."` |
| `n` / `取消` | 跳过 |
| `edit` / `修改为: ...` | 用新 message 重提议 |

### 执行约束
- ✅ 允许 `git status` / `git add` / `git commit -m "..."`
- ❌ 禁止 `git push`（必须拒绝并提示手动 push）
- ❌ 禁止 `--no-verify` / `--force` / `--amend` 等破坏性选项

## 相关文件
- `workflows/verify-flow.md` — 编译→烧录→J-Link→串口四连子流程
- `tools/shared/project_guard.py` — 保护区/增量审计
- `templates/hvr-template.md` — HVR 模板（含执行记录表）
- `workflows/hvr-workflow.md` — HVR 流程细则
- `commands/record.md` — 验证后输出修改记录
