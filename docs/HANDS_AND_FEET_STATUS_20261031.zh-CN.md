# 「手—脚—心脏」落地现状与阻断点（2026-10-31）

更新：2026-10-31
范围：`federation.commander_adapter`、`federation.assignment`、`federation.supervisor`、
`federation.intake`、`coordination.uia_argv`、`wake_adapters/uia_session.ps1`、`windows_ocr_compat.ps1`

这份文档只写**能复算的事实**。凡是没做到的，明确写没做到，不用"已支持"覆盖。

## 结论先行

三件事已经能用：**信箱能派工**、**心跳能驱动**、**一次输入能驱动整条 DAG**。
"手"这一层修掉了两个真缺陷（中文参数被控制台代码页吞掉、OCR 语言不可诊断），
但仍然被**一个机器能力缺口**卡住：本机没有安装中文 OCR 识别器，
所以中文界面的客户端（Qoder / Qoder CN / CodeBuddy CN）的输入框仍然定位不到。

这不是代码问题，是语言包问题，命令在文末。

## 证据阶梯现状

| 阶梯 | 状态 | 证据 |
|---|---|---|
| `CLIENT_BOUND` | 通过 | `visible_windows()` 枚举到 14 个窗口，含 Qoder 22796、ChatGPT 33684、WorkBuddy AI 42508、WorkBuddy 41564、Hermes 2524 |
| `WINDOW_OR_TAB_LOCATED` | 通过 | `coord-uia capture --agent qoder` 返回 `pid: 22796`、`title: Qoder`、`transcript_chars: 810` |
| `INPUT_SUBMITTED` | **未达成** | `input_found: false`；根因见下 |
| `MESSAGE_VISIBLE` | 未达成 | 依赖上一步 |
| `AGENT_ACK` | 未达成 | 依赖上一步 |
| `TASK_RESULT_READBACK` | 未达成 | 依赖上一步 |

注意：`WINDOW_OR_TAB_LOCATED` 通过的含义只是"窗口找到了、截图读到了字"，
**不等于**"消息会发给正确的会话"。这一点沿用 `docs/DESKTOP_SEND_AND_URGENT_INTERJECT.zh-CN.md` 的判定。

## 修掉的缺陷一：中文参数在命令行边界就被吞成 `?`

### 事实

子进程控制台代码页是 **ibm437**：

```
[Console]::InputEncoding  -> ibm437
[Console]::OutputEncoding -> ibm437
```

所以 `-InterjectHints "插话,继续执行并发送"` 到达脚本时已经是 `??,???????`。
同一路径上 `-Text`（派工正文）、`-SessionLabel`（会话名）、`-ComposerHint`（输入框提示）
全部中招。这意味着：**此前任何中文派工单都只能发出问号，且 `strict_composer_hint` 永远匹配不上 OCR 文本。**

复现：

```powershell
# 修复前
$script = "src\agent_evolution_protocol\coordination\wake_adapters\uia_session.ps1"
pwsh -NoProfile -File $script -Agent qoder -ProcessName Qoder -Mode observe `
  -InterjectHints "插话,继续执行并发送,Send while running" -AllowUnknownPath
# -> hints: ['??', '???????', 'Send while running', ...]
```

### 修法

新增 `coordination/uia_argv.py`：把**任何含非 ASCII 的参数值**从 argv 移进一个 UTF-8 JSON 文件，
用 `-ParamFile` 传给脚本；脚本在参数绑定之后 `Set-Variable` 覆盖。
ASCII-only 的调用完全走原路径，行为不变。同时把脚本 stdout 强制为 UTF-8，让回执不再乱码。

修复后同一命令：

```
hints: ['插话', '继续执行并发送', 'Send while running', '继续执行', '在后台发送', ...]
```

覆盖的调用点：`ui_fallback.PowerShellUiaDriver._run`、`commander._uia_deliver`、
`commander._uia_observe`、`commander._capture_uia`。

### 守卫

`tests/test_uia_argv.py`（8 项）锁住拆分规则、列表参数、switch 不误判、临时文件不残留。

## 修掉的缺陷二：OCR 语言不可见，乱码被当成"没找到"

### 事实

`windows_ocr_compat.ps1` 原来只用 `TryCreateFromUserProfileLanguages()`。
本机解析结果是 **en-US**，于是中文界面被识别成拉丁字母近似物：

```
æ/.workbuddy/skills/csdn-auto     # 实际是「我/.workbuddy/skills/...」
äÉjJfE                            # 实际是中文标签
```

英文识别器读中文**不会报错**，只会返回看似合理的拉丁串。结果就是：
`session_evidence_markers`（中文）和 `composer_hint`（中文）永远 miss，
而回执上只看到 `input_found: false`，看不出真正原因。

### 修法

1. `windows_ocr_compat.ps1` 接受 `-Language`，优先 `TryCreateFromLanguage`，失败才回退；
2. 回执新增 `ocr_language` 字段，把实际使用的识别器语言写进证据；
3. `ui_fallback.load_profile` 支持 `ocr_language`（BCP-47 校验），并传给脚本；
4. 四个中文客户端 profile 已设为 `zh-Hans-CN`。

### 现在能看到的事实

```
profile ocr_language: zh-Hans-CN
engine  ocr_language: 'en-US'     <- 回退了，说明中文识别器没装
input_found: False
```

**这条记录就是结论**：代码在请求中文，机器没有中文识别器，于是回退到英文，
所以中文 marker 全部 miss。以前这个推理只能靠猜，现在回执自己写着。

## 唯一的机器级阻断：缺中文 OCR 语言包

需要**管理员** PowerShell：

```powershell
Add-WindowsCapability -Online -Name "Language.OCR~~~zh-CN~0.0.1.0"
```

装完后无需改代码，`engine ocr_language` 会变成 `zh-Hans-CN`，
`session_evidence_markers` 与 `composer_hint` 才开始有匹配可能。

替代路径（不改机器）：
- 客户端若暴露 UIA 控件树，可直接定位输入框。**实测 Qoder 只暴露 3 个元素、无 Edit/Document**，
  Electron 未开无障碍，此路不通；
- 用纯 ASCII 的 `session_evidence_markers`（`FlagGems-sgIang`、`AEP-QODER-DISPATCH`）做会话判定，
  靠 `composer_x_ratio/y_ratio` 几何兜底定位输入框——但这条路径**尚未端到端验证**，不能写成"能用"。

## 三块新机制（可用，有测试）

### 1. 信箱派工：`coord-assign`

一次调用写三件事：DAG 节点、写租约、可执行派工单。

```powershell
aep --coord-root coordination coord-assign `
  --federation-root .agent-evolution `
  --workspace <W-...> --task REVIEW-CLI --target qoder `
  --requirement "复核 cli.py 的 federate 子命令" `
  --paths src/agent_evolution_protocol/cli.py --ttl-hours 24
```

派工单正文是**可执行的**：包含 ACK 行、`coord-claim`、`coord-task-state --phase working/done`、
以及租约续期命令。接收方照做就能留下证据，不需要再开一次对话。
如果 scope 冲突，回执如实写 `claim: scope_conflict`，不假装成功。

### 2. 心跳：`federate step` / `federate serve`

一步做四件事，顺序固定：**先结案 → 再排程 → 再认领 → 最后真发**。

- 结案只看执行者自己写的 `coord-task-state`，不看屏幕、不猜沉默；
- 排程由 `engine.tick` 选人派单（只入队，不碰 UI，避免工作区锁里做 75 秒的 UI 操作）；
- 认领在真发之前：写不了的范围不派；
- 真发交给 `commander.tick`。

`federate serve --interval 30` 是无人值守模式，全部任务 settled 时自动退出。

```powershell
aep --coord-root coordination federate serve `
  --federation-root .agent-evolution --workspace <W-...> `
  --agents qoder codex --interval 30
```

### 3. 一次输入驱动全队：`federate intake`

```powershell
aep --coord-root coordination federate intake `
  --federation-root .agent-evolution --agents alpha beta --shape reviewed `
  --text "把 README 里的命令示例跑一遍并给出通过/失败证据"
```

`--shape reviewed` 生成 `execute → review` 两节点，review 由**另一个** agent 承接
（`preferred` 只在候选里排序，不会把无能力的 agent 提上来）。

实测两跳闭环（隔离目录，`--no-dispatch`）：

```
step 1: execute -> done (alpha)   同时 review -> running (beta)
step 2: review  -> done (beta)    _settled = True
```

## 顺带修掉的语义冲突

`requirement` 原来同时充当「需求文本」和「能力键」，导致真实任务（一句话需求）
匹配不到任何 agent，回执是 `no capable agent`。现在拆成两个字段：

- `capability`：选人用的能力键（缺省回退到 `requirement`，老工作区不受影响）；
- `requirement`：需求原文，可以是一句话。

## 未验证 / 未做

- **端到端真发**：`INPUT_SUBMITTED` 及之后全部未达成，原因见上（缺中文 OCR 包）。
- **紧急插话**：`--urgent-interject` 的分支有测试（`test_ui_fallback.py`），但**没有在真实忙碌会话上跑过**。
  代码不会点停止按钮，缺精确插话控件时只报 `URGENT_INTERJECT_UNAVAILABLE` 并延期。
- **跨重启 + 换策略的真实任务时间线**：仍未做。
- **MCP 适配器、跨机联邦**：未做。`locking.py` 只是本机文件锁。
- **Qoder 之外的客户端**：`codebuddy_cn` / `chatgpt` 的 profile 只是配了 `ocr_language`，没有实测。

## 验收命令

```powershell
# 编码往返（不需要真发消息）
python .tools\probe-encoding.py

# OCR 识别器语言（当前应显示回退后的 en-US）
python -c "from pathlib import Path; from agent_evolution_protocol.coordination import ui_fallback; p=ui_fallback.load_profile(Path('coordination/ui_profiles/qoder.json')); print(ui_fallback.PowerShellUiaDriver().observe(p)['activity_evidence']['ocr_language'])"

# 三块机制的回归
python -m pytest tests/test_uia_argv.py tests/test_federation_assignment.py tests/test_federation_supervisor.py tests/test_federation_commander_adapter.py -q
```
