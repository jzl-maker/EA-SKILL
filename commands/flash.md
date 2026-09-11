# 命令: /ea flash (烧录)

## 功能
调用 OpenOCD 烧录固件到 MCU

## 先探测环境

```bash
python ~/.claude/skills/EA-SKILL/tools/flash-openocd/scripts/openocd_flasher.py --detect
```

确认 OpenOCD 可用且调试探针已连接。

## 调用方式

```bash
python ~/.claude/skills/EA-SKILL/tools/flash-openocd/scripts/openocd_flasher.py \
  --artifact <产物路径> \
  --interface stlink \
  --target target/stm32f4x.cfg
```

## 参数来源

| 参数 | 来源 |
|------|------|
| `--artifact` | 从 build-keil 的产物路径获取（AXF/ELF） |
| `--interface` | 从 `--detect` 探测结果获得（stlink/jlink/cmsis-dap） |
| `--target` | 根据芯片型号选择（GD32F407 → `target/stm32f4x.cfg`） |

## 结果处理

| 字段 | 检查要点 |
|------|----------|
| 烧录状态 | success / failure |
| 校验状态 | verified / skipped |
| 失败分类 | connection-failure / target-response-abnormal / project-config-error |

## 自动决策

- 烧录成功 → 提示下一步（串口观察启动日志）
- 烧录失败 → 根据失败分类引导排查

## 常见错误

| 错误类型 | 原因 | 解决方案 |
|----------|------|----------|
| connection-failure | 调试器未连接或驱动问题 | 检查调试器连接、USB驱动 |
| target-response-abnormal | 芯片未进入调试模式 | 检查芯片供电、复位电路 |
| project-config-error | target 配置文件与芯片不匹配 | 检查 --target 参数 |
| Unsupported transport | --interface 参数错误 | 使用正确的接口类型 (stlink/jlink/cmsis-dap) |
| `Verification failed @ 0x08000000`（J-Link）| **多为误报**——回读固件与待烧 hex 的 MD5 完全一致 | **先比 MD5 再排查**：一致就忽略该报错，别去查接线/供电/复位 |

## 注意事项

⚠️ **--interface 必须与烧录时使用的接口一致**

⚠️ **--target 必须匹配实际芯片型号**

⚠️ **J-Link 报 `Verification failed @ 0x08000000` 多为误报，先核验再排查**

实测该校验报错出现时，**回读 Flash 与待烧 hex 的 MD5 完全一致** —— 固件其实烧对了。
遇到时先核验：把 Flash 对应区域回读成 bin，与 hex 转出的 bin 比对 MD5。

- MD5 **一致** → **忽略这个报错**，固件是好的。**不要**据此去查接线 / 供电 / 复位电路，
  那是往错的方向排查，会白费一整轮。
- MD5 **不一致** → 才是真的烧录失败，按上面「常见错误」排查。

⚠️ **烧录前清掉其它 J-Link 进程**

探针一次只允许一个进程连接。`JLinkRTTLogger.exe`（`/ea debug --rtt start` 起的）/
`JLinkRTTViewer.exe` 还开着时，烧录会连不上或行为异常 —— 采集完 RTT 记得先 `--rtt stop`。
详见 `commands/debug.md` 的「不要和 JLinkRTTViewer 同时开」。

## 相关文件
- `~/.claude/skills/EA-SKILL/tools/flash-openocd/scripts/openocd_flasher.py` - 烧录脚本
- `commands/build.md` - 编译说明
- `commands/serial.md` - 串口监控说明
