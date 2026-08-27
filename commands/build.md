# 命令: /ea build (编译)

## 功能
调用 Keil UV4 编译工程固件

## 调用方式

```bash
python ~/.claude/skills/ea-skill/tools/build-keil/scripts/keil_builder.py \
  --project <工程文件> \
  --target <目标名>
```

## 参数来源

| 参数 | 来源 |
|------|------|
| `--project` | 扫描工作区的 .uvprojx/.uvproj 文件 |
| `--target` | 使用工程中第一个 Target，或由用户指定 |
| UV4 路径 | 自动从 tool_config 读取（由 `/ea setup` 注册） |

## 检测工程

AI 自动在工作区中查找 Keil 工程文件：

```bash
python -c "
from pathlib import Path
for p in Path('.').rglob('*.uvprojx'): print(p)
for p in Path('.').rglob('*.uvproj'): print(p)
"
```

## 结果处理

AI 从脚本 stdout 中提取以下字段记录到 HVR：

| 字段 | 作用 |
|------|------|
| 编译状态 | ✅ 成功 / ❌ 失败 |
| 错误数/警告数 | `错误: N  警告: N` |
| 固件大小 | `Flash ≈ N KB  RAM ≈ N KB` |
| 产物路径 | `产物: file.axf (N KB)` |

## 自动决策

**编译成功 → 自动进入烧录流程**（AI 连续执行，用户只需观察物理现象）

AI 按以下顺序连续执行：

1. **编译** → 成功则自动进入步骤 2
2. **烧录** → 成功则自动进入步骤 3
3. **串口** → 抓取启动日志，用户观察物理现象并口述结果

```
编译(AI执行) → 烧录(AI执行) → 串口监控(AI抓日志) → 用户口述观察结果
```

**编译失败** → 读取编译日志 → 分析错误 → 请求用户修复

## ⚠️ 源文件编码（防乱码）

**本类 Keil 工程源文件为 GB2312（GBK）编码，非 UTF-8。** 修改 `.c`/`.h` 前注意：

- 读取按 `open(path, encoding="gbk")`，不要按 UTF-8 读（中文会乱码）
- **禁止用 UTF-8 编辑器直接改源文件中文注释**——整文件会被转成 UTF-8，全部中文变 `\xEF\xBF\xBD`，编译报 `missing closing quote`
- 修改用字节级 Python 操作保持 GBK，注释尽量用 ASCII；改动前建议留备份
- 编译日志是 GBK/UTF-8 混合，keil_builder.py 已自动探测回退解码

## 常见错误

- ❌ 未找到 .uvprojx/.uvproj 工程文件 → 确认工程文件路径
- ❌ UV4 路径配置错误 → 运行 `/ea setup` 重新配置
- ❌ 编译错误 → 显示错误行号和内容，分析原因
- ❌ 源文件中文乱码 / missing closing quote → 源文件被误转成 UTF-8，从备份恢复并保持 GBK 编码

## 相关文件
- `~/.claude/skills/ea-skill/tools/build-keil/scripts/keil_builder.py` - 编译脚本
- `commands/flash.md` - 烧录说明
- `commands/serial.md` - 串口监控说明
