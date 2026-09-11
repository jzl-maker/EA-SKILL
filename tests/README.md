# 回归测试

针对 `tools/` 下工具脚本的回归测试。**不需要硬件、不需要 Logic 2、不需要第三方
Python 包** —— 全部用合成数据或假可执行文件驱动，只要有 `py` 就能跑。

## 运行

```bash
cd tests
for t in test_*.py; do echo "== $t"; py "$t" || echo "!! $t FAILED"; done
```

每个脚本自包含，单独跑也行。退出码 0 = 全过，1 = 有用例失败。

## 套件

| 文件 | 覆盖 | 用例数 |
|------|------|--------|
| `test_waveform.py` | `digital.csv` 解析、通道统计、内置 UART 解码、`decoded.csv` 有损扫描 | 21 |
| `test_spi_i2c.py` | 内置 SPI / I2C 解码：四种 SPI mode、CS 切帧、MSB/LSB、CPOL 自检、I2C START/ACK/地址读写位、接线错误 | 40 |
| `test_la_cli.py` | `/ea la` CLI 层：`--dry-run` 通道显示、触发抓取、内置解码接线、有损告警 | 33 |
| `test_drain.py` | `--rtt snapshot` 的 RdOff 写回（缓冲写满导致目标丢日志/锁死那个缺陷） | 13 |
| `test_fixes.py` | RTT 占用检测阻断、错误归因（地址错 vs 探针被占） | 7 |
| `test_watermark.py` | `doc_reader` 水印过滤，含**正常文档不被误伤**的反向断言 | 22 |

## 假可执行文件

`test_drain.py` / `test_fixes.py` 通过 `fake_jlink.bat` → `fake_jlink.py` 模拟
JLink Commander 的 `savebin` / `w4` 通道，忠实实现**单消费者环形缓冲**语义
（写不下就整条丢弃、`w4` 写回 RdOff 才释放空间）——不这样测不出"读取端不推进
RdOff"这个缺陷。`fake_fail.bat` 模拟连接失败。

两个套件各自用独立的状态文件（`_drain_state.json` / `_fixes_state.json`）表示
目标侧 RAM，避免互相串扰；这些文件是运行产物，已在 `.gitignore` 中忽略。

## 给它加用例

容易踩的两个坑：

- **被测模块用 `exec` 而非 import 加载**（`require_module`），所以 `test_la_cli.py`
  往 `sys.modules` 里塞假的 `saleae.automation`；`test_waveform.py` 则要自己把模块
  注册进 `sys.modules`，否则 `@dataclass` 解析字符串注解时会找不到本模块。
- **别让用例依赖外部环境**。`test_fixes.py` 曾用真实进程扫描，机器上只要真开着
  `JLinkRTTViewer` 就会被占用检查提前拦下，4 个用例集体假红——凡是"某个变量是
  用例前提"的，都要显式打桩固定它。
