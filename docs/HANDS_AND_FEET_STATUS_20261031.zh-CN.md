# 「手—脚—心脏」落地现状与阻断点（2026-10-31）

更新：2026-10-31 晚（第三次修订，`INPUT_SUBMITTED` 起已实测达成）
范围：`federation.commander_adapter`、`federation.assignment`、`federation.supervisor`、
`federation.intake`、`coordination.uia_argv`、`wake_adapters/uia_session.ps1`、`windows_ocr_compat.ps1`

这份文档只写**能复算的事实**。凡是没做到的，明确写没做到，不用"已支持"覆盖。

## 更正声明（重要）

上一版这份文档说"唯一阻断是机器缺中文 OCR 语言包"，并给了一条 `Add-WindowsCapability` 命令。
**那个结论是错的。** 实测：

```
[Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages
-> en-US
-> zh-Hans-CN          # 中文识别器本来就装着
```

`C:\Windows\servicing\Packages\Microsoft-Windows-LanguageFeatures-OCR-zh-cn-Package~...mum` 也一直在。
真正的原因是**代码缺陷**：`-OcrLanguage` 只接到了 10 个 OCR 调用点里的 1 个。按错误结论去装包的人会撞上
`Add-WindowsCapability: 没有注册类`（PowerShell 7 的 DISM 模块问题），白折腾一趟。

## 结论先行

三件事已经能用：**信箱能派工**、**心跳能驱动**、**一次输入能驱动整条 DAG**。

"手"这一层本轮修掉三个真缺陷，中文界面现在**能读**了：

```
ocr_language: 'zh-Hans-CN'          <- 识别器语言正确
transcript_chars: 3046              <- 修前是 0
state: ready                        <- 修前是 unknown
ready_hits: ["发送", "Send", "Submit"]      <- 中文 marker 第一次匹配上
interject_hits: ["插话", "继续执行并发送"]  <- 插话提示也能识别了
```

但 `INPUT_SUBMITTED` 仍未达成，原因换成了另一个、更明确的缺口：
Qoder 以管理员运行，普通权限的执行器读不到它的令牌（`target_elevation: unknown`），
UIPI 会拦下所有输入注入。详见「剩余阻断」。

**第三次修订（当晚）**：把执行器提到管理员后，这个缺口补上了。同一条命令、同一个窗口、
同一个 `AEP-UI-PROBE-20261031T203037`，一次跑通到 `AGENT_ACK`。详见
「提权执行器实测：`INPUT_SUBMITTED` → `MESSAGE_VISIBLE` → `AGENT_ACK`」。

## 证据阶梯现状

| 阶梯 | 状态 | 证据 |
|---|---|---|
| `CLIENT_BOUND` | 通过 | `visible_windows()` 枚举到 14 个窗口，含 Qoder 22796 |
| `WINDOW_OR_TAB_LOCATED` | 通过 | `pid: 22796`、`title: Qoder`、`transcript_chars: 75210`、`transcript_source: uia` |
| `ACTIVITY_STATE_READ` | 通过 | `state: ready`、`ready_hits: ["发送","Send","Submit"]` |
| `INPUT_SUBMITTED` | 通过（2026-10-31 晚） | `input_found: true`、`input_candidate_count: 2`、`write_method: uia-valuepattern`、`input_verified: true`、`composer_cleared: true` |
| `MESSAGE_VISIBLE` | 通过（2026-10-31 晚） | `status: message-visible`、`task_delivered: true`、`send_method: enter`、`send_retry: paste+enter`、`composer_clear_polls: 36` |
| `AGENT_ACK` | 通过（2026-10-31 晚） | 读回会话：`ACK AEP-UI-PROBE-20261031T203037`，出现在我们自己那句话之外（`lines_outside_our_message: 1`），时间戳 20:31 |
| `TASK_RESULT_READBACK` | 部分 | `observe` 能读回屏幕文本（`transcript_chars: 75210`）；「按 ACK 里的任务号取回结果」还没有对应的读取器 |

`WINDOW_OR_TAB_LOCATED` 通过的含义只是"窗口找到了、截图读到了字"，
**不等于**"消息会发给正确的会话"。这一点沿用 `docs/DESKTOP_SEND_AND_URGENT_INTERJECT.zh-CN.md` 的判定。

上表的数字来自操作者命令（`coord-uia`）。调度引擎（`ui_fallback.run`）另跑了一次，
同样到达 `MESSAGE_VISIBLE` 并拿回挑战应答：`send_status: message-visible`、
`send_retry: paste+enter`、`composer_clear_polls: 16`、读回 `AEP-ACK: C0F5868E719945`。
应答比引擎的 60 秒窗口晚到约一分钟，所以引擎自己记的是 `NO_REPLY_WITHIN_TIMEOUT`
（`task_delivered: true`、`agent_accepted: false`）——见「调度路径实测」。

## 缺陷一：中文参数在命令行边界就被吞成 `?`

子进程控制台代码页是 **ibm437**，所以 `-Text` / `-SessionLabel` / `-ComposerHint` /
`-InterjectHints` 里的中文到达脚本时已经是 `?`。修复前：

```
hints: ['??', '???????', 'Send while running', ...]
```

修法：新增 `coordination/uia_argv.py`，把任何含非 ASCII 的参数值从 argv 移进 UTF-8 JSON，
用 `-ParamFile` 传入；脚本在参数绑定之后 `Set-Variable` 覆盖。ASCII-only 调用完全走原路径。
脚本 stdout 同时固定 UTF-8，回执不再乱码。

修复后：`hints: ['插话', '继续执行并发送', 'Send while running', '继续执行', ...]`

守卫：`tests/test_uia_argv.py`（8 项）。

## 缺陷二：OCR 语言只接到 1/10 个调用点

`Get-WindowOcrText` 有 10 个调用点，只有 `Get-ActivityEvidence` 里那一个传了 `-Language`。
其余九个（会话定位、发送按钮定位、正文读回、粘贴后校验）全部走默认空语言，
于是回退到用户配置识别器 **en-US**。英文识别器读中文**不报错**，
只返回看似合理的拉丁串（`æ/.workbuddy` 实际是「我/.workbuddy」），
所以中文 marker 永远 miss，而回执只显示 `input_found: false`。

修法：函数级默认值改为继承脚本作用域，一个点覆盖全部调用：

```powershell
param([IntPtr]$Hwnd, [string]$Language = '')
# $script: 是显式的：裸 $OcrLanguage 依赖动态作用域，
# 而正是这种"看起来接上了"让它一直没接上。
if (-not $Language) { $Language = $script:OcrLanguage }
```

同时回执新增 `ocr_language_requested`，把"请求的语言"和"实际用的语言"分开写，
这类缺陷下次自己就暴露了。

## 缺陷三：中文 OCR 逐字插空格

即使语言对了，Windows OCR 输出的是 `插 话 发 送`，而 marker 写成 `插话`，
`Contains('插话')` 永远为假。修法放在 OCR 的唯一出口 `windows_ocr_compat.ps1`：

```powershell
$cjk = '[\u3400-\u4DBF\u4E00-\u9FFF\u3000-\u303F\uFF00-\uFFEF]'
[regex]::Replace($Value, "(?<=$cjk)\s+(?=$cjk)", '')
```

只合并**两个中文字之间**的空白；`Send while running` 的空格、以及中英文交界处的空格都保留
（把中文和英文粘在一起会同时毁掉两者）。逐行文本和整体文本都过一遍，否则只修一半症状。

守卫：`tests/test_ocr_wiring.py`（8 项）。它从 ps1 里读出 `$cjk` 和替换式再跑，
所以改脚本时测试会跟着变，不会变成第二套实现。

## 顺带修掉的第四个缺口：`observe` 读不到 Electron 的文本

`observe` 的 transcript 原来只走 `Get-VisibleTranscript`（纯 UIA）。
Electron 客户端不暴露 Document/Edit，所以 `transcript_chars` 恒为 0 —— 这会被误读成"窗口里没字"，
实际是"我看不见"。现在当 UIA 文本为空且请求了 OCR 时回退到 OCR，并用 `transcript_source` 标明来源。

## 剩余阻断：是权限，不是通路（第二次更正）

上一版这里写「`deliver` 没有几何兜底、`-AllowComposerGeometryFallback` 只接在 `compose` 上」。
**这条也要更正**：Qoder 的 `transport` 是 `ocr`，`deliver` 本来就会切到 `compose` 模式
（`"-Mode", "compose" if profile["transport"] == "ocr" else "deliver"`），
`-AllowComposerGeometryFallback`、`-ComposerXRatio/YRatio` 也一直在传。兜底通路是通的。

真正缺的是两处，本轮已修：

1. **`-AllowClipboardPaste` 从未从 Python 侧传过。** 脚本的守卫是
   `if (-not $selection -and -not $AllowClipboardPaste) { throw 'NO_INPUT_CONTROL' }`，
   也就是「读不到可编辑控件时，没有出路」。现在按 profile 的
   `allow_clipboard_paste` 显式开启，默认关闭——粘贴落在激活时获得焦点的位置，
   脚本自己的报错写着 "only with human supervision"。
2. **`coord-uia` 读的是 `cli.py` 里硬编码的 `UIA_CLIENTS`，不是 `ui_profiles/*.json`。**
   那份表里 Qoder 还停在 `0.4.11`（运行中是 `0.5.1`），也不带 `-AllowUnknownPath`，
   于是操作者的命令直接 `LAUNCH_FORBIDDEN: no window for this agent`，
   而调度器用同一份 profile 却能找到窗口。现在改为 profile 文件优先、内置表只作兜底，
   参数映射收成 `ui_fallback.profile_args()` 一份，两条路共用。

修完实测（普通权限终端，提权 Qoder）：

```
status: transcript-captured      pid: 22796      transcript_chars: 2100   # OCR 读回
profile_path_check: {"expected": "...\\0.5.1\\Qoder.exe",
                     "actual":   "...\\0.5.1\\Qoder.exe", "match": true}
target_elevation: unknown        executor_elevation: standard
drivable: false                  blocked_by: TARGET_NEEDS_ELEVATION
```

`profile_path_check` 是新加的：适配器自己的路径校验用 `Process.MainModule`，
对提权进程直接抛异常并被吞掉，等于**恰好在最需要它的地方静默跳过**。
`coordination/process_info.py` 改用 `QueryFullProcessImageNameW`
（只需 `PROCESS_QUERY_LIMITED_INFORMATION`），提权目标也能读到真实镜像路径。

所以现在 `INPUT_SUBMITTED` 只剩一个条件：**执行器以管理员启动**。
`deliver` 第一次真跑仍然要有人在场——它会真的往会话里打字。

## 提权执行器实测：`INPUT_SUBMITTED` → `MESSAGE_VISIBLE` → `AGENT_ACK`

用 `Start-Process -Verb RunAs` 把执行器提到管理员，其余不变（同一个窗口、同一条命令、
同一个事件号）。这一跑之前，先修掉三个各自独立、且互相掩盖的缺陷——它们让"已经送达"
在回执上看起来像"没送到"。

### 缺陷四：回执在出口处被控制台编码器毁掉

`Write-Report` 把 JSON 交给 PowerShell 的输出流，于是控制台编码器进了链路。提权进程的
OEM 代码页表示不了中文。实测对照：

```
-OutFile 落盘的采集文件：52,750 个非 ASCII 字节，仅 7 个 '?'   <- 完美 UTF-8
同一份 JSON 回执走 stdout：中文全部变成 '?'                     <- 只有回显被毁
```

工作成功了，只有回执被毁——这种证据比没有证据更坏，因为它指向错误的方向。
修法：`Write-Report` 直接向标准输出写字节，绕开编码器。

```powershell
$bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($json + "`n")
$stream = [Console]::OpenStandardOutput()
$stream.Write($bytes, 0, $bytes.Length)
$stream.Flush()
```

### 缺陷五：`coord-uia` 在提权下崩溃

提权后 `locale.getpreferredencoding()` 变成 **cp1252**（非提权时是 UTF-8），
而适配器写的是 UTF-8 中文。`subprocess.run(text=True)` 的读线程死在第一个中文字符上，
`stdout` 变成 `None`：

```
AttributeError: 'NoneType' object has no attribute 'splitlines'
```

修法：改走 `uia_argv.run()`——和驱动器同一个执行器，理由也一样（非 ASCII 的 `-Text`
过不了控制台代码页；回执固定 UTF-8，不管是谁起的进程）。另外三处
（`transcript_patrol`、`octo_transport`、`wake_daemon`）是同一个模式的同一类缺陷，
一并加了显式 `encoding="utf-8"`：缺陷在模式里，不在某一条命令上。

### 缺陷六：发送只校验一次，而且校验得太早

会话正文**本来就包含输入框**，所以读回循环可能第一次轮询就命中，而应用还没处理发送。
此时 composer 检查只跑一次、看到事件号还在框里，就报 `submitted-unconfirmed`——
消息其实已经送达。**这里的假阴性比假阳性更坏：它会诱发重复发送。**

现在的顺序是：轮询到 composer 清空；若仍不清空，则通过获得焦点的控件**粘贴**再发一次。
重试不是"等慢一点"的补丁——`ValuePattern.SetValue` 更新了视觉编辑框但**不触发** web 应用的
input 事件，发送按钮因此保持禁用，Enter 也没有反应。回执新增 `composer_clear_polls`
与 `send_retry`，把走过的路径写出来，而不是让人去猜。

### 实测回执（`AEP-UI-PROBE-20261031T203037`，Qoder pid 22796）

```
status: message-visible          task_delivered: True
input_found: True                input_candidate_count: 2
write_method: uia-valuepattern   input_verified: True
send_method: enter               send_retry: paste+enter
composer_cleared: True           composer_clear_polls: 36
drivable: True                   path_verified: True
target_elevation: elevated       executor_elevation: elevated
```

随后从会话里读回，事件号出现在我们自己那句话之外：

```
transcript_chars: 75210    event_id_occurrences: 10
lines_outside_our_message: ["ACK AEP-UI-PROBE-20261031T203037 | ACK AEP-UI-PROBE-20261031T203037"]
```

附带一个副产品：无障碍树这次真的通了。`input_candidate_count: 2`、
`transcript_source: uia`（不再靠 OCR）、composer 是 `ControlType.Edit` / `loop-rich-editor`、
发送按钮按颜色可定位。`SPI_SETSCREENREADER` 让 Chromium 建树之后，ChatGPT 侧
元素从 3 个变成 400 个，Qoder 侧同理。

## 调度路径（`ui_fallback.run`）也通了，且先修了四件事

上面那一条走的是**操作者命令**（`coord-uia`）。调度器和心跳走的是另一条代码
`ui_fallback.run`，它更严格：要验会话、验模型，才允许输入。第一次真跑它是失败的，
失败点逐层暴露了四个缺陷：

### 缺陷七：打不动就先说，别烧掉 75 秒再谎称「可能发了」

`annotate_elevation` 一直把 `drivable` / `blocked_by` 写在回执上，但**没人据此行动**。
普通权限执行器对着提权客户端会走完整个 UI 尝试，最后记成
`CEASED / SUBMISSION_UNCONFIRMED: possible send`——而 UIPI 保证一个字都没打进去。

现在 `ui_fallback.run` 在观察到窗口之后、任何写操作之前判定，返回 `EXECUTOR_TOO_LOW`；
`commander` 记成可续跑的 `DEFERRED`。两侧实测（探针的 `deliver` 故意抛异常，两种情况都不可能真打字）：

| 执行器 | state | capture | deliver |
|---|---|---|---|
| 普通权限 | `EXECUTOR_TOO_LOW` / `TARGET_NEEDS_ELEVATION` | 0 | 0 |
| 管理员 | 过闸门，走到 deliver 才被探针拦下 | 1 | 1 |

### 缺陷八：profile 的会话标记和真实窗口差一个字

```
SESSION_LABEL_NOT_UNIQUE_IN_SIDEBAR: 0
```

对照 74,891 字符的真实读回：`赛事守冠执行` 0 次 / `赛事夺冠执行` 5 次；
`FlagGems-sgIang` 0 次 / `FlagGems-sglang` 8 次。于是**每一次**调度都在会话校验处
`DEFERRED`，且永远等不到 ready。profile 已按实测改正；`SESSION_NOT_VERIFIED` 回执现在
写出找的是哪个字符串、在不在、读回多少字符。

### 缺陷九：`transport: ocr` 已经不是该走的通道

改对标记后仍失败在 `send_status: composer-not-confirmed`——OCR/几何兜底的 compose 通路
读不回输入框内容，于是拒绝按 Enter（这是对的）。`transport` 改为 `uia` 后，同一段代码
立刻得到 `message-visible` + `send_retry: paste+enter`。无障碍树能建起来之后，OCR 只该是退路。

### 缺陷十：「已送达」不能因为「没回话」被抹掉

消息 `message-visible` 之后等回复，超时后回执里 `task_delivered` 仍是初始的 `false`。
现在 `task_delivered` 来自发送回执、`agent_accepted` 只认目标自己的应答，状态名改为
`NO_REPLY_WITHIN_TIMEOUT`（只陈述确定的事实）。

### 调度路径实测

```
send_status: message-visible   send_retry: paste+enter   composer_clear_polls: 16
读回: AEP-ACK: C0F5868E719945      <- 正是那次的 expected_ack（反序校验串）
```

探针 20:54 发出，引擎 60 秒窗口内没等到回复，20:55 会话里出现了应答。
**送达和应答都发生了，只是应答比窗口晚**——因此 `reply_timeout_seconds` 由 60 提到 120。
迟到的应答目前**没有被回收**（没有只读步骤去把订单升级成已接受），这是下一步。

## 三块新机制（可用，有测试）

### 1. 信箱派工：`coord-assign`

一次调用写三件事：DAG 节点、写租约、可执行派工单。派工单正文是**可执行的**：
包含 ACK 行、`coord-claim`、`coord-task-state --phase working/done`、租约续期命令。
scope 冲突时如实返回 `claim: scope_conflict`，不假装成功。

### 2. 心跳：`federate step` / `federate serve`

一步四件事，顺序固定：**先结案 → 再排程 → 再认领 → 最后真发**。

- 结案只看执行者自己写的 `coord-task-state`，不看屏幕、不猜沉默；
- 排程由 `engine.tick` 选人派单（只入队，不碰 UI，避免工作区锁里做 75 秒的 UI 操作）；
- 认领在真发之前：写不了的范围不派；
- 真发交给 `commander.tick`。

`federate serve --interval 30` 是无人值守模式，全部任务 settled 时自动退出。

### 3. 一次输入驱动全队：`federate intake`

`--shape reviewed` 生成 `execute → review` 两节点，review 由**另一个** agent 承接
（`preferred` 只在候选里排序，不会把无能力的 agent 提上来）。实测两跳闭环（隔离目录）：

```
step 1: execute -> done (alpha)   同时 review -> running (beta)
step 2: review  -> done (beta)    _settled = True
```

## 顺带修掉的语义冲突

`requirement` 原来同时充当「需求文本」和「能力键」，导致一句话需求匹配不到任何 agent
（回执 `no capable agent`）。现在拆成 `capability`（选人键，缺省回退）与 `requirement`（需求原文）。

## 未验证 / 未做

- **端到端真发**：`INPUT_SUBMITTED` / `MESSAGE_VISIBLE` / `AGENT_ACK` 已达成，两条代码都验过——
  操作者命令（`AEP-UI-PROBE-20261031T203037`）和调度引擎（`AEP-SCHED-PROBE-20261031T1`，
  挑战应答 `AEP-ACK:C0F5868E719945`）。但**只跑通了这一条路径**：`--urgent-interject`
  没有在真实忙碌会话上验过，`codebuddy_cn` 没有实测，跨重启的时间线也没有。
- **迟到的应答没有被回收**：`NO_REPLY_WITHIN_TIMEOUT` 之后没有只读步骤去读回 `expected_ack`
  并把订单升级成已接受。现在只能靠人再看一眼。
- **`TASK_RESULT_READBACK`**：能读回屏幕文本，但"按任务号取回结果并判定完成"还没有读取器。
- **紧急插话**：`--urgent-interject` 有测试，且插话提示现在**能被 OCR 识别**（`interject_hits` 命中），
  但**没有在真实忙碌会话上跑过**。代码不会点停止按钮。
- **跨重启 + 换策略的真实任务时间线**：仍未做。
- **MCP 适配器、跨机联邦**：未做。`locking.py` 只是本机文件锁。
- **Qoder 之外的客户端**：`chatgpt` 已实测（`input_found: true`、3 个输入候选、60978 字符），
  `codebuddy_cn` 只配了参数、未实测。
- **视觉腿（v0.6.7）一次都没真跑过**：`vision_grounding` / `policy_executor` / `input_routing`
  三层都有测试、都能离线回放（`aep coord-vision`、`aep coord-policy`、`aep coord-route`），
  但 `mage_vl_cowork` 仍是 `candidate`，`ui_fallback.run()` 也仍只走 UIA。
  所以「视觉能定位 composer」这件事在本机**尚未验证**，只验证了「没有 grounder 时不编数字」。

## 验收命令

```powershell
# 识别器语言（应显示 zh-Hans-CN，不是 en-US）
python .tools\probe-argv.py

# 中文 OCR 读回（应显示数千字符的中文，且不含"插 话"这种字间空格）
python .tools\probe-capture.py

# 本轮回归
python -m pytest tests/test_ocr_wiring.py tests/test_uia_argv.py `
  tests/test_federation_assignment.py tests/test_federation_supervisor.py `
  tests/test_federation_commander_adapter.py tests/test_federation_conformance.py -q
```
