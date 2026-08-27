# tools/resource — 固件资源分析（/ea size + /ea map）

解析 Keil 链接器生成的 `.map` 文件，提供固件内存资源分析。**纯标准库，无第三方依赖**（复用 `scripts/map_parser.py`）。

| 工具 | 命令 | 功能 |
|------|------|------|
| `scripts/size_tool.py` | `/ea size` | Flash/RAM/栈/堆 统计 + 超限预警 |
| `scripts/map_tool.py` | `/ea map` | 对象排行 / 分段分布 / 符号地址映射 / 栈堆详情 |
| `scripts/map_parser.py` | 共享 | Keil .map 解析核心（armcc v5 / armclang v6）|

## 用法

```bash
py scripts/size_tool.py --project <工程目录>          # 自动扫描最新 .map
py scripts/size_tool.py --map <file.map> --flash-kb 512 --ram-kb 144
py scripts/map_tool.py --map <file.map> --objects --top 10
py scripts/map_tool.py --map <file.map> --symbols __initial_sp
py scripts/map_tool.py --map <file.map> --regions
```

## 支持的 .map 格式

Keil MDK（armcc v5 / armclang v6）。解析五类数据：

1. **总量** `Total RO/RW/ROM Size` → Flash = ROM（Code+RO+RW），RAM = RW（RW+ZI）
2. **Execution Region** → 分段布局 + `Max`（容量来源，链接脚本区域上限）
3. **Image component sizes** → 逐对象 Code/RO/RW/ZI 排行
4. **Symbol Table**（Image Symbol Table + Global Symbols）→ 符号地址/大小/类型
5. **STACK/HEAP Section** → 栈/堆分配量（startup 定义值，非运行期峰值）

GCC 的 ld 输出 .map 格式不同，v1 不支持。

## 容量与预警

- 容量缺省 = 匹配 `IROM|FLASH|ROM` / `IRAM|RAM` 的 Region `Max`
- `--flash-kb/--ram-kb` 覆盖（芯片容量 ≠ 链接脚本时）
- 预警：使用率 ≥ `--warn`（默认 90）⚠️，≥ `--err`（默认 95）❌

## 边界说明

栈/堆统计的是 startup 定义的**分配量**。运行期实际栈峰值请用 `/ea debug` 读 `__initial_sp - SP`。
