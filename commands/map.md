# 命令: /ea map (Map 文件解析)

## 功能

解析 Keil 编译生成的 `.map` 文件，输出 **对象内存排行 / 内存分段分布 / 符号地址映射 / 栈堆详情**，用于定位内存大户、核对链接布局、查符号地址。

| 能力 | 参数 | 数据来源 |
|------|------|----------|
| 对象内存排行 | `--objects [N]` | Image component sizes（逐 .o 的 Code/RO/RW/ZI）|
| 内存分段分布 | `--regions` | Execution Region（exec/load base、size、max、使用率）|
| 符号排行/查询 | `--symbols [FILTER]` | Global Symbols + Image Symbol Table |
| 栈/堆详情 | `--stack` | STACK/HEAP Section + `__initial_sp`/`__heap_base` 地址 |

**适用**：Keil MDK 工程（armcc v5 / armclang v6 的 .map 格式）。GCC/其他工具链暂不支持。

## 调用方式

```bash
py <SKILL>/tools/resource/scripts/map_tool.py --project <工程目录>
# 自动扫描工程下最新 .map → 默认对象排行

# 显式指定 .map
py .../map_tool.py --map build/xxx.map --objects          # 对象排行（默认）
py .../map_tool.py --map build/xxx.map --objects --top 5  # 只看前 5

# 内存分段分布
py .../map_tool.py --map build/xxx.map --regions

# 符号排行（size>0 按字节降序）
py .../map_tool.py --map build/xxx.map --symbols

# 精确查符号地址映射（名字子串 或 0x 地址）
py .../map_tool.py --map build/xxx.map --symbols __initial_sp

# 栈/堆详情
py .../map_tool.py --map build/xxx.map --stack
```

## 参数表

| 参数 | 必须 | 说明 |
|------|------|------|
| `--map <file>` | 是（动作） | 显式指定 .map 文件 |
| `--project <dir>` | 是（动作） | 工程目录，扫描最新 .map |
| `--objects` | 是（动作，默认） | 对象内存排行 |
| `--regions` | 是（动作） | 内存分段分布 |
| `--symbols [FILTER]` | 是（动作） | 符号排行；带名字/地址过滤则精确查询 |
| `--stack` | 是（动作） | 栈/堆详情 |
| `--top N` | 否 | 排行条数（默认 20）|
| `--json` | 否 | 结构化 JSON 输出（整个解析结果）|
| `--dry-run` | 否 | 只打印流程不解析 |

## 工作流程（AI 执行闭环）

```
1. 定位 .map     --project 扫描，或 --map 显式指定
2. 按需取数：
   --objects    哪个 .o 占内存最多（字库/协议栈/业务模块）
   --regions    代码/数据落在哪些区，各区使用率（核对链接脚本）
   --symbols    找内存大户变量/函数，或查某符号的地址（配合 /ea debug 断点）
   --stack      栈/堆分配量 + 初始 SP 地址
3. 分析         内存占比是否合理 → 定位优化对象或异常区
```

示例闭环：`/ea map --objects --top 10` → ehfont_init.o（.constdata 字库 25KB）占大头 → 判断字库是否该挪外部 Flash。

## 注意事项

1. **排行口径**：对象按 `Code+RO+RW+ZI` 总量降序；符号只列 `size>0` 的（Section/Data/Code 类型），`Number` 调试符号已过滤。
2. **`--symbols FILTER` 是子串匹配**，`0x` 开头按地址精确匹配；空过滤（`--symbols` 不带值）则排行。
3. **栈/堆是分配量**：STACK/HEAP 是 startup 定义值；运行期峰值用 `/ea debug` 读 SP 测量。
4. **GBK 编码兼容**：工程路径含中文时 .map 可能是 GBK 编码，解析器自动回退。

## 常见错误

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| 未找到 .map 文件 | 未编译或 .map 不在扫描目录 | 先 `/ea build`；`--map` 显式指定 |
| 非 Keil .map 格式 | 工具链不是 Keil | v1 仅支持 Keil armcc/armclang |
| 无匹配符号 | 过滤名不存在或符号未导出 | 用更短子串重查；确认符号在 .map 里（非 static 内联被优化掉）|

## 相关文件
- `<SKILL>/tools/resource/scripts/map_tool.py` - 工具脚本
- `<SKILL>/tools/resource/scripts/map_parser.py` - 共享 .map 解析器
- `commands/size.md` - 同源命令（/ea size，总量 + 超限预警）
