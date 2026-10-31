# 「手—脚—心脏」落地现状与阻断点（2026-10-31）

更新：2026-10-31（第二次修订，纠正上一版的错误结论）
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

但 `INPUT_SUBMITTED` 仍**未达成**，原因换成了另一个、更明确的缺口：
Qoder 是 Electron 且未开无障碍，UIA 里没有可编辑控件（`input_found: false`），
而 `deliver` 模式**没有几何兜底**——`-AllowComposerGeometryFallback` 只接在 `compose` 模式上。
详见「剩余阻断」。

## 证据阶梯现状

| 阶梯 | 状态 | 证据 |
|---|---|---|
| `CLIENT_BOUND` | 通过 | `visible_windows()` 枚举到 14 个窗口，含 Qoder 22796 |
| `WINDOW_OR_TAB_LOCATED` | 通过 | `pid: 22796`、`title: Qoder`、`transcript_chars: 3046`、`transcript_source: ocr` |
| `ACTIVITY_STATE_READ` | 通过 | `state: ready`、`ready_hits: ["发送","Send","Submit"]` |
| `INPUT_SUBMITTED` | **未达成** | `input_found: false`；`deliver` 无几何兜底，见下 |
| `MESSAGE_VISIBLE` | 未达成 | 依赖上一步 |
| `AGENT_ACK` | 未达成 | 依赖上一步 |
| `TASK_RESULT_READBACK` | 未达成 | 依赖上一步（但 `observe` 已能读回屏幕文本） |

`WINDOW_OR_TAB_LOCATED` 通过的含义只是"窗口找到了、截图读到了字"，
**不等于**"消息会发给正确的会话"。这一点沿用 `docs/DESKTOP_SEND_AND_URGENT_INTERJECT.zh-CN.md` 的判定。

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

## 剩余阻断：`deliver` 没有几何兜底

事实：

- `-AllowComposerGeometryFallback` 只出现在 `compose` 模式（第 1013、1052 行）；
- `deliver` 的守卫是 `if (-not $selection -and -not $AllowClipboardPaste) { throw 'NO_INPUT_CONTROL' }`；
- `-AllowClipboardPaste` **从未从 Python 侧传过**（`ui_fallback` / `commander` 里都没有）；
- Qoder 的 profile 里 `allow_composer_geometry_fallback: true`、`composer_x_ratio: 0.37`、
  `composer_y_ratio: 0.92` 都配好了，但 `deliver` 不读它们。

也就是说：**配置齐了，通路没接**。要跑通 `INPUT_SUBMITTED`，需要让 `deliver` 在
`$selection` 为空时按 profile 的比例算一个点击点，走「点击 → 剪贴板粘贴 → Enter → OCR 读回 EventId」。
这条路径会真的往会话里打字，必须在有人在场时第一次跑。

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

- **端到端真发**：`INPUT_SUBMITTED` 及之后未达成，原因是 `deliver` 缺几何兜底，不是 OCR。
- **紧急插话**：`--urgent-interject` 有测试，且插话提示现在**能被 OCR 识别**（`interject_hits` 命中），
  但**没有在真实忙碌会话上跑过**。代码不会点停止按钮。
- **跨重启 + 换策略的真实任务时间线**：仍未做。
- **MCP 适配器、跨机联邦**：未做。`locking.py` 只是本机文件锁。
- **Qoder 之外的客户端**：`codebuddy_cn` / `chatgpt` 只配了 `ocr_language`，未实测。

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
