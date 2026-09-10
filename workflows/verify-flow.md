# 工作流: verify-flow（嵌入式验证四连）

> 由 `commands/verify.md` 加载。EA-SKILL 纯嵌入式，此流程即默认验证流程。

## 流程总览

```
通用 verify 流程
   │
   ├─ 四连执行：
   │   ├─ ① build-keil       （编译固件）
   │   ├─ ② flash-openocd    （烧录，含校验）
   │   ├─ ③ jlink-debug      （J-Link 运行验证）
   │   └─ ④ serial-monitor   （抓取业务启动日志）
   │
   └─ 用户口述物理现象观察 → HVR 文件
```

**关键**：AI 连续自动执行四步，用户只需观察物理现象并口述结果。

## ① 编译（build-keil）

```bash
python ~/.claude/skills/EA-SKILL/tools/build-keil/scripts/keil_builder.py \
  --project <工程文件路径> \
  --target <目标名>
```

- `--project`：扫描工作区 `*.uvprojx`/`*.uvproj`
- UV4 路径：从 `tool_config`（`/ea setup` 注册）读取

结果提取（写 HVR）：编译状态 / 错误·警告数 / 固件大小 / 产物路径。
**成功 → 自动进烧录；失败 → 分析日志请求修复。**

## ② 烧录（flash-openocd）

```bash
python ~/.claude/skills/EA-SKILL/tools/flash-openocd/scripts/openocd_flasher.py --detect
python ~/.claude/skills/EA-SKILL/tools/flash-openocd/scripts/openocd_flasher.py \
  --artifact <产物路径> \
  --interface <stlink|jlink|cmsis-dap> \
  --target <target/xxx.cfg>
```

- 校验状态：verified / skipped
- 失败分类：connection-failure / target-response-abnormal / project-config-error
**成功 → 自动进 J-Link 运行验证。**

## ③ J-Link 运行验证（jlink-debug）

烧录后**用 J-Link 确认固件真的在跑**（不依赖串口是否已配好）：

```bash
# 读寄存器：确认 PC 进入用户区（main），SP 合法
py ~/.claude/skills/EA-SKILL/tools/jlink-debug/scripts/jlink_debug.py --regs

# 抓 RTT 启动日志（先 start 再复位，避免错过启动消息）
py ~/.claude/skills/EA-SKILL/tools/jlink-debug/scripts/jlink_debug.py --rtt start --log .ea/logs/rtt.log
# → 触发复位 → 
py ~/.claude/skills/EA-SKILL/tools/jlink-debug/scripts/jlink_debug.py --rtt stop --log .ea/logs/rtt.log
py ~/.claude/skills/EA-SKILL/tools/jlink-debug/scripts/jlink_debug.py --rtt show --log .ea/logs/rtt.log --tail 50

# 必要时读固件区内存比对
py ~/.claude/skills/EA-SKILL/tools/jlink-debug/scripts/jlink_debug.py --mem 0x08000000 --count 8
```

判定：
- PC 落在 Flash 用户区（如 `0x0800xxxx`）且 SP 合法 → 固件已启动 ✅
- RTT 抓到启动日志 → 启动路径确认 ✅
- PC 停在 0x1FFFxxxx 系统区 / 看门狗复位循环 → 启动异常，进入 `/ea debug` 定位

> ⚠️ IWDG 看门狗：halt 期间不喂狗会复位，读寄存器/内存要快；RTT start 必须先于复位。

**成功 → 自动进串口。**

## ④ 串口（serial-monitor）

```bash
python ~/.claude/skills/EA-SKILL/tools/serial-monitor/scripts/serial_monitor.py \
  --port COM5 --baud 115200 --duration 15 \
  --wait-reset --auto-reset \
  --interface <同上> \
  --openocd-config interface/stlink.cfg \
  --openocd-target target/stm32f4x.cfg \
  --save .ea/logs/serial_S<N>.log
```

1. 打开串口监听 → 2. OpenOCD `reset halt` 复位 → 3. MCU 重启输出完整日志 → 4. 保存到 `logs/`
GUI（人工观察）：`python ~/.claude/skills/EA-SKILL/tools/serial-mcp/serial_monitor.py --project "%CD%" --step "S<N>"`

常见错误：不传 `--interface` → Unsupported transport；不传 `--openocd-config` → invalid command name。

## HVR 字段（嵌入式执行记录）

verify 的 HVR 文件追加：

```markdown
## 嵌入式执行记录
| 步骤 | 工具 | 结果 |
|------|------|------|
| 编译 | build-keil | <成功/失败> + 产物路径 |
| 烧录 | flash-openocd | <成功/失败> + interface + verified |
| J-Link 验证 | jlink-debug | <PC 地址 / RTT 日志 / 异常现象> |
| 串口 | serial-monitor | <抓到启动日志/未抓到> |

### 物理现象（用户口述）
- <用户观察>
```

## 相关文件
- `commands/verify.md` — verify 入口（含保护区审计）
- `commands/build.md` / `flash.md` / `serial.md` / `debug.md` — 四连工具细则
- `templates/hvr-template.md` — HVR 模板
