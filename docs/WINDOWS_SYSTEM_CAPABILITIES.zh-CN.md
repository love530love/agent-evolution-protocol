# 该交给 Windows 的能力，和不该交的（2026-10-31 实测）

## 结论

**机制外包给系统，证据留在自己手里。**

窗口枚举、元素树、文本读写、输入注入、事件通知、进程会话事实——这些 Windows 做得比我们好，
应当调用而不是自研。任务 DAG、证据阶梯、写租约、交接包、哈希链审计——这些系统不提供，
用 OS 能力替换等于放弃可复算性，必须自己持有。

## 实测：一个开关把「只能 OCR」变成「400 个元素」

Chromium（因此所有 Electron 客户端：Qoder / ChatGPT / Claude / WorkBuddy）**只有在系统报告
「有屏幕阅读器」时才构建无障碍树**。这个开关是 `SPI_SETSCREENREADER`，Chromium 通过
`WM_SETTINGCHANGE` 感知，所以**已经在运行的窗口也会生效，不用重启**。

同一窗口、同一时刻，开关前后的差别：

| | 元素数 | 输入候选 | `input_found` | 文本来源 | 字符数 |
|---|---|---|---|---|---|
| ChatGPT 开关前 | 3 | 0 | `false` | OCR | 靠识别 |
| ChatGPT 开关后 | **400** | **3** | **`true`** | **UIA** | **60978** |

这不是「识别得更准」，是**换了一条通道**：从「看像素猜」变成「问系统要」。有了 `ValuePattern`
可以直接写输入框，不用剪贴板；有了 `TextPattern` 可以直接读正文，不用 OCR。

注：Chromium 的树一旦建起来不会因为开关关闭而拆掉，所以同一进程内测不出「关掉就变回 3 个」。
因果来自机制（Chromium 源码按 `SPI_GETSCREENREADER` 决定是否建树），不是来自这次 A/B。

## 一个系统级硬约束：完整性级别

Qoder 现在跑在**提权**状态下。实测：

```
coord-a11y status --pid 22796
-> target_elevation: unknown, drivable: false
```

`unknown` 是 Windows 拒绝交出更高完整性级别进程的令牌。后果是连锁的：
`OpenProcessToken` 打不开 → `ShowWindow` 返回 0 → UIA 枚举 0 个元素 → 注入的按键被 UIPI 丢弃。

**这不是代码能绕过的。** 执行方的完整性级别必须 ≥ 目标进程。所以：

- 目标普通权限 → 执行方普通权限即可（ChatGPT 实测 `drivable: true`）；
- 目标提权 → 执行方也必须提权，或者把目标改成普通权限启动。

把这条写进 `coord-a11y` 的回执，比让三步之后的输入静默失败要好。

### 本机采用的操作规则

客户端**一律以管理员运行**——这是有意的：Agent 自己执行命令时，非提权会被 UAC 卡住，
任务就停在那里。既然目标恒为高完整性级别，那就让**执行器同样以管理员启动**。

方向性只要求单向：提权进程可以驱动低权限窗口，反过来不行。所以执行器提权后，
所有客户端（无论提权与否）都能驱动。

```powershell
# 在管理员终端里启动无人值守循环
aep --coord-root coordination federate serve --interval 30 --agents qoder codex
```

验证一行：

```powershell
aep coord-a11y status --pid <目标pid>     # 期望 "drivable": true
```

两个配套事实：

- 项目在 `K:`，`GetDriveTypeW` 返回 `FIXED`（本地盘），提权进程可见。
  若把仓库放到**网络映射盘**，提权后会看不到该盘符——那是 UAC 的经典坑，与本项目无关但会咬人。
- 驱动层会在回执里写 `target_elevation` / `drivable` / `blocked_by`。
  看到 `blocked_by: TARGET_NEEDS_ELEVATION` 就是权限不匹配，不是选择器写错了。

## 已经落地的

`coord-a11y`（`coordination/a11y.py`）：

```powershell
aep coord-a11y status --pid <目标pid>   # 开关状态 + 能否驱动
aep coord-a11y check  --pid <目标pid>   # 自动开启（若关着）+ 双方完整性级别
aep coord-a11y enable [--persist]      # 开启；--persist 才写注册表，默认重启即恢复
aep coord-a11y disable                 # 关闭
```

默认**只改运行时**（不写 `SPIF_UPDATEINIFILE`），所以重启即恢复，不会永久改变用户环境。
测试不翻转这个开关（见 `tests/test_a11y.py` 顶部说明），只读状态 + 一次「设成当前值」的空往返。

## 还该交给系统、但还没做的

| 能力 | 现在怎么做的 | 系统给的正解 | 收益 |
|---|---|---|---|
| 文本输入 | `SendKeys` / 剪贴板粘贴 | `SendInput` + `KEYEVENTF_UNICODE` | 逐字符注入 Unicode，**绕开代码页和输入法**；`uia_argv.py` 那套 UTF-8 参数文件是在给「没用这个 API」打补丁 |
| 写入输入框 | 点击 + 粘贴 | UIA `ValuePattern.SetValue` | 不依赖焦点、不污染剪贴板、不会点偏 |
| 感知变化 | 定时截屏 + OCR | `SetWinEventHook`（`EVENT_OBJECT_VALUECHANGE`、`EVENT_SYSTEM_FOREGROUND`） | 事件驱动，省 CPU，延迟从秒级降到毫秒级 |
| 进程会话事实 | WMI（本机已不可用）/ toolhelp | `NtQueryInformationProcess` + Job Object + `WTSEnumerateSessions` | 已有部分实现，需统一到一处 |
| 唤醒/保活 | 文件轮询 | 任务计划程序 / 命名管道 / 服务 | 真正无人值守，不依赖某个终端窗口活着 |

## 边界：这些不要外包

- **任务与依赖**：DAG 节点、`deps`、就绪判定。系统没有这个概念。
- **证据阶梯**：`CLIENT_BOUND → … → TASK_RESULT_READBACK`。屏幕能读到字 ≠ 消息发对了人。
- **写租约与冲突**：`claim` + TTL + `self_overlaps`。文件锁能保证互斥，保证不了「谁有权改」。
- **交接与接续**：三区快照 + handoff packet。这是 AEP 存在的理由。
- **审计链**：`seq` 连续 + `prev_sha256` 链接。OS 日志给不了这个语义。

## 第三方执行生态

`examples/execution-provider-catalog.json` 里已登记 `trycua/cua`（本机 `cua-driver 0.38.0`，
状态 `candidate`，`TOOLS_DISCOVERED`，**从未执行过桌面输入**）。它的
`get_accessibility_tree` / `type_text` / `press_key` / `verify_state` 正好覆盖上表的前三行。

推荐顺序：**先让本机 UIA 通道跑通**（已验证可行），再把 Cua 作为同一 `Adapter` 接口下的备选
Provider。不要两套并行上线——协议项目里「第二套并存的标准」比「没有标准」更有害。
