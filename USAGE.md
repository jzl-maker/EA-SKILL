# EA-SKILL 使用说明

让 AI 直接接管单片机的编译、烧录、串口、调试、查寄存器、看波形。
你说要什么，它敲命令、读数据、给结论。

---

## 20 个命令

| 命令 | 用途 | 要硬件 |
|------|------|--------|
| `/ea setup` | 环境初始化 | — |
| `/ea init` | 新建空工程 | — |
| `/ea si` | 接入已有工程（代码审计） | — |
| `/ea rec` | 恢复项目上下文（只读） | — |
| `/ea new` | 新功能开发（含需求澄清） | — |
| `/ea doc` | PDF / Word / Excel 解析 | — |
| `/ea test` | 生成 unit / board 测试 | — |
| `/ea build` | Keil 编译（成功后自动接烧录） | — |
| `/ea flash` | 烧录（OpenOCD / J-Link 双通道） | 探针 |
| `/ea serial` | 串口监控 | USB 转串口 |
| `/ea debug` | RTT / 断点 / 内存 / 寄存器 | J-Link 或 ST-Link/DAP |
| `/ea svd` | SVD 寄存器地图（`--read` 需硬件） | 部分 |
| `/ea la` | Saleae 逻辑分析仪 | 分析仪（`--simulate` 无需） |
| `/ea scope` | 示波器（Rigol / DS100 CSV） | Rigol 或 CSV 文件 |
| `/ea size` | Flash / RAM 用量 + 超限预警 | — |
| `/ea map` | .map 文件解析 | — |
| `/ea verify` | 步骤验证（HVR + 保护区审计） | — |
| `/ea record` | 输出修改记录 | — |
| `/ea stat` | 状态查看 | — |
| `/ea help` | 帮助 | — |

> **`/ea xxx` 不是快捷命令，是口令。** AI 靠语义识别——你说「帮我烧录一下」和 `/ea flash` 等价。
> 没有自动补全，别等提示。命令定义在 `commands/<命令>.md`，觉得哪步不对可以直接打开看或改。
> 每个命令的完整参数：`/ea help <命令>`，或直接问 AI。

---

## 安装

### 方式 A：从 GitHub 直装

在 Claude Code 里说：

```
帮我安装 https://github.com/jzl-maker/EA-SKILL.git 这个 skill
```

AI 会克隆仓库、装到技能目录。

### 方式 B：从压缩包安装

**1.** 拿到 `EA-SKILL.zip`。仓库目录下自己打一个：

```bash
git archive -o EA-SKILL.zip HEAD
```

**2.** 在 Claude Code 里说：

```
帮我安装本地的 D:\EA-SKILL.zip，解压到 ~/.claude/skills/EA-SKILL
```

（路径按你的客户端换，见下面「装到哪个目录」。）

### 装完都要跑一句初始化

```
/ea setup
```

看到 5 步全 ✅ 就成了：

```
🔧 EA-SKILL 环境初始化
[1/5] 更新权限配置...   ✅
[2/5] 探测工具...       ✅
[3/5] 注册工具路径...   ✅
[4/5] 检查 OpenOCD...   ✅
[5/5] 注册 CLAUDE.md 触发器...   ✅
```

`/ea setup` 干五件事：探测工具路径（OpenOCD / Keil UV4 / J-Link）、缺 OpenOCD 就自动下载、写权限配置、把命令索引写进 `~/.claude/CLAUDE.md`。

> **第 5 步是 `/ea` 生效的关键**——它建立「编译/烧录/串口/RTT/断点/SVD/需求/文档」关键词 → 命令文档的指针表。没这步 AI 不知道「烧录」该去读哪个文件。

### 装到哪个目录

**装到哪都行。** 命令文档里的脚本路径用的是 `<SKILL>` 占位符（指 skill 根目录），AI 执行前会自己换算成真实路径。

| 你的客户端 | 默认技能目录 |
|-----------|-------------|
| Claude Code | `~/.claude/skills/` |
| Cursor | `~/.cursor/skills/`，也兼容读 `~/.claude/skills/` |
| Trae | `.trae/skills/`（工程内） |
| 其它 | 该客户端文档里写的技能目录 |

> ⚠️ **只有一件事仍依赖 Claude Code**：`/ea setup` 的第 1 步（写 `~/.claude/settings.json` 权限）和第 5 步（写 `~/.claude/CLAUDE.md` 触发器）是 Claude 的机制。
> 在别的客户端里，这两步要换成该客户端的等价物——权限用它的黑名单配置，触发器用它的规则文件。
> **`/ea setup` 的第 2~4 步（探测工具路径）跟客户端无关，照常可用。**

---

## 5 分钟上手

### 1. 接入你的工程

**在工程目录下**打开 Claude Code，敲：

```
/ea si .
```

它会扫出芯片型号、外设占用、模块结构、保护区清单，并在工程根建一个 `.ea/` 目录——**这是 AI 对这个工程的记忆**。

以后新开对话，一句 `/ea rec` 就把上下文捞回来。

> 空工程用 `/ea init <项目名>`，不要用 `si`。

### 2. 编译 + 烧录

```
/ea build
```

编译成功后它**自动接着烧录、再抓串口日志**。你只管看板子。

### ✅ 到这你就上手了

---

## 常用速查

命令不用给参数——AI 会问你，或者自己从 `context.md` 里找。

### 环境与项目

| 我要… | 命令 | 也可以直接说 |
|-------|------|-------------|
| 装好 / 修好环境 | `/ea setup` | 初始化一下环境 |
| 接入已有工程 | `/ea si` | 接入这个工程 |
| 新建空工程 | `/ea init` | 新建一个工程叫 xxx |
| 新对话里捞回上下文 | `/ea rec` | 继续上次的活儿 |
| 我现在做到哪了 | `/ea stat` | 现在什么进度 |

### 写代码

| 我要… | 命令 | 也可以直接说 |
|-------|------|-------------|
| 加新功能 | `/ea new` | 加一个 500ms 定时翻转 LED |
| 只要方案不动代码 | `/ea new` | 先给方案，别写代码 |
| 解析 PDF / Word / Excel | `/ea doc` | 读一下这个数据手册 |
| 生成单元测试 | `/ea test` | 给 drv_led.c 写单测 |
| 生成板级测试用例 | `/ea test` | 给按键功能写板级用例 |
| 验证这一步 | `/ea verify` | 验证 s5 |
| 输出修改记录 | `/ea record` | 记录一下 s5 |

### 构建、烧录、运行

| 我要… | 命令 | 也可以直接说 |
|-------|------|-------------|
| 编译（**成功后自动接烧录**） | `/ea build` | 编译一下 |
| 只编译不烧录 | `/ea build` | 编译，先别烧 |
| 强制全量重编 | `/ea build` | 全量重编 |
| 烧录 | `/ea flash` | 烧到板子上 |
| 先看看探针在不在 | `/ea flash` | 看看探针连上没 |
| 抓串口日志 | `/ea serial` | 抓一下串口日志 |
| 抓复位后的完整启动日志 | `/ea serial` | 复位，抓完整启动日志 |

### 调试

| 我要… | 命令 | 也可以直接说 |
|-------|------|-------------|
| 看代码跑到哪了 | `/ea debug` | 看看 Display_Refresh 跑到没有 |
| 读变量 | `/ea debug` | 看看 _TimeCount_10ms 现在多少 |
| 监控变量**且不停 CPU** | `/ea debug` | 不停机盯着 _TimeCount_10ms |
| 抓 RTT **历史**日志 | `/ea debug` | 抓一下 RTT 日志 |
| 复位并放行（配合人工测试） | `/ea debug` | 复位然后放行，我来按按键 |
| 读 CPU 寄存器 | `/ea debug` | 看看 CPU 寄存器 |
| 未知符号名先查一下 | `/ea debug` | 查一下含 LED 的符号 |
| 源码级调试 | `/ea debug` | 起 gdb 调试 |

### 寄存器与仪器

| 我要… | 命令 | 也可以直接说 |
|-------|------|-------------|
| 列出可用 SVD | `/ea svd` | 有哪些 SVD |
| 看外设有哪些寄存器 | `/ea svd` | 看看 GPIOA 有哪些寄存器 |
| 看寄存器的位域定义 | `/ea svd` | 查一下 RCC 的 CR 寄存器 |
| 读寄存器当前值（不停机） | `/ea svd` | 读一下 RCC 的 CR |
| 模糊搜寄存器名 | `/ea svd` | 搜一下含 HSE 的寄存器 |
| 示波器抓波形 | `/ea scope` | 抓一下 CHAN1 的波形 |
| 示波器测量 | `/ea scope` | 测一下 CHAN1 的频率和占空比 |
| 逻辑分析仪抓取 | `/ea la` | 抓一下 0~3 通道 |
| 逻辑分析仪 + 解码 | `/ea la` | 抓一下 I2C，SCL 在 3 号脚 |
| 无硬件试一下 | `/ea la` | 用模拟设备跑一遍 |

### 资源分析

| 我要… | 命令 | 也可以直接说 |
|-------|------|-------------|
| Flash / RAM 用量 | `/ea size` | 看看 Flash 用了多少 |
| 谁占了内存 | `/ea map` | 谁占内存最多 |
| 查符号地址 | `/ea map` | 查一下 __initial_sp 的地址 |
| 栈/堆详情 | `/ea map` | 看看栈多大 |

### 通用

**任何命令加 `--dry-run` 都只打印计划、不真跑。**

`--json` 给 AI 读（`svd` / `la` / `scope` / `size` / `map` / `doc` 支持）——你在终端里手跑别加，人类可读输出更友好。

---

## 六个必须知道的坑

**1. `/ea xxx` 是口令，不是命令。**
见开头「20 个命令」下的说明。记住一点就够：**说人话一样管用**。

**2. AI 有 6 条红线，但只有一条是硬拦。**
保护区禁改（`startup_*.s` / 链接脚本 / `system_*.c`）、老工程只做增量、改动必给 diff、动手前先读 context、Keil 的 `.c`/`.h` 是 GB2312、**AI 只提议 commit 不 push**。

> Claude Code 里 `rm` / `sudo` / `git push` 被 `~/.claude/settings.json` **硬拦**；
> **换到 Cursor / Trae / Copilot 就没有这层了**，得在那个客户端里自己配黑名单。

**3. J-Link 一次只能一个进程连。**
`JLinkRTTViewer` / `JLinkRTTLogger` / `JLinkGDBServerCL` / Keil 调试会话都会独占探针。
**被占用时的现象和「探针没插」一模一样**——连不上先排查占用。

> 例外：RTTViewer 和 Keil 同时开着**不阻止烧录**。别把烧录失败归因到它头上。

**4. Windows 装了 SEGGER 驱动，OpenOCD 就用不了 J-Link 探针。**
报 `LIBUSB_ERROR_NOT_SUPPORTED` + `No J-Link device found`。硬约束，`transport select swd` 也救不了。
让 AI 换 J-Link 原生通道就行。

**5. `--mem` 默认 halt CPU，抓 RTT 默认写目标 RAM。**
人工按按键的测试要让它**不停机**（前提是固件冻结了看门狗 IWDG）。
抓 RTT 是「读走即消费」，同一段历史只能读一次——**要留存必须让它落盘**。

**6. 增量编译不打印固件大小，这是正常的。**
Keil 没重新链接就不输出 `Program Size:`，字段缺席不代表编译有问题。要拿大小用 `/ea size`。

---

## 排错

### 命令不生效

| 现象 | 解决 |
|------|------|
| 敲 `/ea xxx` 没反应 | 重跑 `/ea setup`，确认第 5 步 ✅ |
| AI 说要先初始化 | 缺 `.ea/`，跑 `/ea init` 或 `/ea si .` |
| AI 说已初始化但读不到状态 | 你在子目录里，切到工程根 |

### 工具找不到

| 现象 | 解决 |
|------|------|
| `python` 命令不可用 | 用 `py`（Windows 启动器，`python` 可能是 Store 假别名） |
| 找不到 OpenOCD / J-Link / UV4 | 重跑 `/ea setup` 注册路径 |
| 中文输出乱码 | 命令行直跑加 `PYTHONIOENCODING=utf-8` |
| 某工程要用专用工具路径 | 在工作区放 `.ea_skill.json`，覆盖全局 `%APPDATA%/ea_skill/config.json`<br>`py tools/shared/tool_config.py list` 查当前配置 |

### 烧录 / 调试失败

| 现象 | 解决 |
|------|------|
| `LIBUSB_ERROR_NOT_SUPPORTED` + `No J-Link device found` | 见坑 4 |
| `There already is an active connection` | 探针被占用，见坑 3 |
| 连不上，现象和「探针没插」一样 | 同上。**先排查占用** |
| J-Link 报 `Verification failed` | **多半是误报**——看独立回读：回读一致且 md5 与产物相同就是成功了 |
| 刚烧完立刻回读拿到错位镜像 | 编程后需要时间收敛，让 AI 的 `--settle` 保持默认 10 秒，**别调 0** |
| 串口打不开 | COM 口被占用，关掉串口助手 / 其它终端 |
| 调试 halt 后设备复位了 | 有 IWDG 看门狗。缩短 halt 时间，或改用不停机模式 |
| 抓 RTT 拿不到历史日志 | RTTViewer 把缓冲排空了，先关掉它 |
| `地址无效：…不是 SEGGER_RTT_CB` | `_SEGGER_RTT` 符号地址不对，与探针无关 |

### 其它

| 现象 | 说明 |
|------|------|
| `git diff` 是空的但确实改了代码 | 仓库无提交、或仓库根是工程的父目录时，`git diff` 会**静默返回空**。AI 会用基线比对兜底 |
| Keil 源码中文变乱码 | 工程是 GB2312 被 UTF-8 编辑器转了。**不可逆**，改动前先备份 |
| 示波器数和面板对不上 | 采样域和仪器 `:MEAS?` 是两套算法，采样域更准（实测 28800Hz → 采样域 +0.01% / 仪器 −0.79%） |

---

## 可选依赖

不用就不装。

| 你要用的功能 | 需要什么 |
|-------------|---------|
| `/ea build` `/ea size` `/ea map` | Keil MDK |
| `/ea flash`（ST-Link/DAP）、免断点调试 | OpenOCD（`/ea setup` 可自动下载） |
| `/ea flash`（J-Link）、`/ea debug` | J-Link + SEGGER 驱动。**不需要 OpenOCD** |
| `/ea doc` 解析 PDF | `py -m pip install pdfplumber`（**DOCX / XLSX 零依赖**） |
| `/ea la` | Saleae Logic 2 软件 + `py -m pip install logic2-automation` |
| `/ea scope`（Rigol） | `py -m pip install pyvisa pyvisa-py numpy`；USB 直连需 WinUSB 驱动（Zadig）或 NI-VISA |
| `/ea scope` 解析 DS100 CSV | **什么都不用装** |
| `/ea serial` | `py -m pip install pyserial` |

> 仪器可以先不装——`/ea la --simulate`（Logic 2 模拟设备）和 `/ea scope --parse-tmc`（合成波形）能无硬件跑通全流程。

---

## 已验证状态

**已实机验证**：烧录（ST-Link / J-Link 原生）、示波器抓取测量（Rigol DS1074Z / DS2302A / DS100 CSV）、SVD 寄存器地图（N32G4FR）、回归测试 199 条全绿。

**待验证**：`/ea debug` OpenOCD 免断点通道。

**跑回归测试**（不需要任何硬件）：

```bash
cd tests && for t in test_*.py; do py "$t" || echo "!! $t FAILED"; done
```

---

## 开发

源码在 `tools/<name>/scripts/*.py` + `commands/<cmd>.md`。

```bash
# 改完部署到技能目录
cp -r EA-SKILL/. ~/.claude/skills/EA-SKILL/
diff -rq EA-SKILL ~/.claude/skills/EA-SKILL    # 验证一致
```

**新增命令流程**：写脚本 → 写 `commands/<cmd>.md` → 更新 `SKILL.md` 命令表 → 更新 `templates/claude-md-snippet.md` → 部署 → 重跑 `/ea setup`。
