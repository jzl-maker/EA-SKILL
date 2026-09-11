# 命令: /ea size (固件资源分析)

## 功能

解析 Keil 编译生成的 `.map` 文件，统计固件 **Flash / RAM / 栈 / 堆** 使用情况，并支持**超限预警**（使用率 ≥ warn 警告，≥ err 超限）。

| 能力 | 数据来源 | 说明 |
|------|----------|------|
| Flash 用量 | Total ROM Size（Code + RO Data + RW Data）| 对比 Flash 容量出使用率 |
| RAM 用量 | Total RW Size（RW Data + ZI Data）| 对比 RAM 容量出使用率 |
| 栈/堆 | Global Symbols 里 STACK / HEAP Section | ⚠️ startup 定义的**分配量**，非运行期峰值 |
| 超限预警 | 使用率 vs `--warn/--err` 阈值 | ✅ 正常 / ⚠️ 偏高 / ❌ 超限 |
| 容量来源 | Execution Region 的 `Max`（链接脚本区域上限）| `--flash-kb/--ram-kb` 可覆盖 |

**适用**：Keil MDK 工程（armcc v5 / armclang v6 的 .map 格式）。GCC/其他工具链暂不支持。

## 调用方式

```bash
py ~/.claude/skills/EA-SKILL/tools/resource/scripts/size_tool.py --project <工程目录>
# 自动扫描工程下最新 .map → 输出 Flash/RAM 使用率 + 栈/堆 + 预警

# 显式指定 .map（编译产物在 Listings/ 或 build/ 下）
py .../size_tool.py --map build/xxx.map

# 覆盖容量 + 收紧阈值（芯片实际容量与链接脚本不一致时）
py .../size_tool.py --map build/xxx.map --flash-kb 512 --ram-kb 144 --warn 85 --err 92

# AI 解析用 JSON
py .../size_tool.py --map build/xxx.map --json
```

## 参数表

| 参数 | 必须 | 说明 |
|------|------|------|
| `--map <file>` | 是（动作） | 显式指定 .map 文件 |
| `--project <dir>` | 是（动作） | 工程目录，扫描最新 .map |
| `--flash-kb N` | 否 | Flash 容量 KB（缺省用链接脚本 region Max）|
| `--ram-kb N` | 否 | RAM 容量 KB（缺省用链接脚本 region Max）|
| `--warn` | 否 | 预警阈值 %（默认 90）|
| `--err` | 否 | 超限阈值 %（默认 95）|
| `--json` | 否 | 结构化 JSON 输出 |
| `--dry-run` | 否 | 只打印流程不解析 |

## 工作流程（AI 执行闭环）

```
1. 定位 .map     --project 扫描，或 --map 显式指定（通常 Listings/ 或 build/ 下）
2. 容量确定      缺省 region Max；芯片手册容量与链接脚本不符时 --flash-kb/--ram-kb 覆盖
3. 读取总量      Flash = Total ROM，RAM = Total RW
4. 计算使用率    used/capacity × 100 → ✅/⚠️/❌
5. 栈/堆         STACK/HEAP 分配量（startup 定义）
6. 预警判断      任一 ≥ warn 提示，≥ err 标超限 → 结论（是否逼近容量上限）
```

示例闭环：`/ea size --project . --flash-kb 512 --ram-kb 144` → Flash 21.8% ✅、RAM 50.4% ✅、栈 32KB → 判断固件内存余量充足。

## 注意事项

1. **栈/堆是分配量**：STACK/HEAP 是启动文件里定义的大小（本工具读 .map 符号值），不是运行期实际峰值。实际峰值栈用 `/ea debug` 读 SP（`__initial_sp - SP`）测量。
2. **容量缺省取链接脚本 region Max**：`.sct` 里 `LOAD_REGION`/`RW_IRAM1` 的 Max 就是芯片可用量；若链接脚本人为缩小区域（如给 BootLoader 预留），Max 会小于芯片容量，此时用 `--flash-kb/--ram-kb` 覆盖成芯片真实值。
3. **ZI-data 计入 RAM**：Total RW = RW Data + ZI Data（含 bss 未初始化段）。
4. **GBK 编码兼容**：工程路径含中文时 .map 可能是 GBK 编码，解析器自动回退。
5. **⚠️ Flash 口径与 Keil 面板可能差几百字节 —— 两个数都对，别当成 bug**。

   | 来源 | Flash 取什么 |
   |------|-------------|
   | `/ea size` | `.map` 的 **`Total ROM Size` 行** |
   | Keil 面板 / `keil_builder.py` | **Code + RO-data + RW-data 三项相加** |

   实测某工程：`/ea size` = 106,104 B，Keil 三项和 = 73,680 + 32,240 + 728 = **106,648 B**，
   差 **544 B**（= RW-data 728 与计入 ROM 的初始化量 184 之差）。

   **怎么用**：同一工程内**只用一种口径**看趋势，别混着比。做超限预警时按**较大**的那个
   口径留余量（Keil 三项和更保守）。两个数相差几百字节时**不要**去查工具 bug。

## 常见错误

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| 未找到 .map 文件 | 未编译或 .map 不在扫描目录 | 先 `/ea build`；`--map` 显式指定 Listings/ 下文件 |
| 非 Keil .map 格式 | 工具链不是 Keil（GCC/Renesas 等）| 本工具 v1 仅支持 Keil armcc/armclang |
| Flash/RAM 使用率 100%+ | 链接脚本容量小于实际用量 | 检查 .sct 区域 Max 是否过小；`--flash-kb` 覆盖 |

## 相关文件
- `~/.claude/skills/EA-SKILL/tools/resource/scripts/size_tool.py` - 工具脚本
- `~/.claude/skills/EA-SKILL/tools/resource/scripts/map_parser.py` - 共享 .map 解析器
- `commands/map.md` - 同源命令（/ea map，排行/分段/符号映射）
