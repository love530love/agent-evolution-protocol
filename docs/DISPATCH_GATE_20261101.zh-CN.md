# 派工门禁与窗口身份（2026-11-01）

这份文档回答一个具体问题：**Qoder CN 那次内容发错，代码里到底是哪一步允许了它。**
答案有两处，都在代码里，都能复现，也都已经修了。

## 0. 结论先行

| 问题 | 是不是 UI 问题 | 处置 |
|---|---|---|
| 窗口身份只靠标题子串 | 否，是匹配规则 | `qoder` 曾匹配 `Qoder CN` 窗口；改为「exe 路径仲裁」 |
| 发送前没有任何内容校验 | 否，是缺失的闸门 | 新增 `dispatch_gate`，12 项检查，任一硬项不过就**不发** |

**这一版没有做「用户确认」这一步**，理由是它和 7×24 无人值守直接冲突。取而代之：
所有硬检查默认阻断、可离线复算（`aep coord-dispatch`），并且在回执里写明每一项是
`checked` 还是「没查」。

## 1. 窗口身份：`Qoder` 是 `Qoder CN` 的子串

`client_registry.matches()` 的兜底分支是「进程名出现在窗口标题里就算匹配」。实测：

```
qoder profile  vs 'Qoder CN' 窗口 : True      <- 错的
qoder profile  vs 'Qoder'    窗口 : True
qoder_cn prof  vs 'Qoder'    窗口 : False     <- 不对称
```

只跑 Qoder CN 的机器上，发给 `qoder` 的派工单会**找到并进入 Qoder CN 的窗口**，
而且 `unique: True`——一个错误的窗口，被描述成「唯一、无歧义」。
这正是「窗口与会话绑定不够牢固」的代码级形态。

### 修法：exe 只当仲裁者，不当过滤器

不能让 exe 变成过滤条件。本机实测四条 profile 里**两条已经漂移**：

| agent | profile 里的 exe | 真实 exe | 结论 |
|---|---|---|---|
| `qoder` | `D:\Programs\Qoder\Qoder.exe` | `...\.qoder-versions\0.5.1\Qoder.exe` | 漂移 |
| `qoder_cn` | `...\0.4.11\Qoder CN.exe` | `...\0.5.1\Qoder CN.exe` | 漂移 |
| `workbuddy` | `D:\Programs\WorkBuddy\WorkBuddy.exe` | 同 | 一致 |
| `workbuddy_ai` | `D:\Programs\WorkBuddyAI\WorkBuddyAI.exe` | 同 | 一致 |

如果 exe 不匹配就算「没找到」，上面两个 Qoder 会**同时变成不可达**——用一个「发不出去」
换掉一个「发错」，不是明显更好的交易。所以规则是：

> 当某个窗口的可执行文件**正是另一个已登记 profile 声明的路径**时，那个 profile 拥有它，
> 其他 profile 的匹配作废。

版本目录漂移不会触发这条（路径对不上任何别人的声明），跨产品误配会（`Qoder` 与
`Qoder CN` 装在不同目录）。

### 本机真实发现（2026-11-01，只读）

```
chatgpt      -> [('ChatGPT',  'title', 'ChatGPT.exe')]        unique=True
codebuddy_cn -> [('FlagGems-sglang - CodeBuddy CN', 'exe')]   unique=True
qoder        -> [('Qoder',    'title', 'Qoder.exe')]          unique=True
qoder_cn     -> [('Qoder CN', 'title', 'Qoder CN.exe')]       unique=True
workbuddy    -> [('WorkBuddy','exe',   'WorkBuddy.exe')]      unique=True
workbuddy_ai -> [('WorkBuddy AI','exe','WorkBuddyAI.exe')]    unique=True

--- rejected ---
workbuddy refused 'WorkBuddy AI' owner= workbuddy_ai
```

**`workbuddy` 之前一直在把 WorkBuddy AI 的窗口算成自己的**，而两个客户端当时都在跑。
这条是仲裁跑出来的，不是推测出来的。

`identity` 现在随每个窗口上报：`exe`（强）/ `title`（中）/ `process-substring`（弱）。
弱身份在门禁里是告警，`--strict` 下是阻断。

## 2. 发送前门禁：`coordination/dispatch_gate.py`

位置在 `commander._dispatch()`，**取到窗口之后、选传输之前**。放在这里而不是每个传输里，
是因为「哪段文字发给谁」是订单的属性；按传输各写一份，下一个新增的传输就会漏掉。

12 项检查，纯函数，不碰窗口、不碰进程、不碰网络——**只有这样才能在桌面出问题时仍然可靠**。

| 检查 | 强度 | 抓什么 |
|---|---|---|
| `content_hash_recorded` | 阻断 | 有 envelope 却没有内容哈希 → 这一版无法自证 |
| `content_unchanged` | 阻断 | 入队后被改过的正文 |
| `one_event_id` | 阻断 | 正文里有第二个 event id（旧任务内容混入） |
| `one_task_marker` | 阻断 | 有多个 `[AEP:<task>]`，或那一个不是本单的 |
| `event_id_in_text` | 阻断 | 正文里找不到本单 event id |
| `ack_requirement_in_text` | 阻断 | 没有 `AEP-ACK` 要求 |
| `target_matches_profile` | 阻断 | 订单目标是 `qoder`，绑定的 profile 是 `qoder_cn` |
| `not_superseded` | 阻断 | 同一 task 已有更新的订单 |
| `dag_event_matches` | 阻断 | 工作区 DAG 里的 event id 与订单不一致 |
| `not_expired` | 阻断 | 已过期 |
| `task_named_in_text` / `assignee_named_in_text` / `workspace_named_in_text` | 告警（`--strict` 升为阻断） | 正文没有自报家门 |
| `window_identity` | 告警（`--strict` 升为阻断） | 窗口只靠进程名子串绑定 |

后四项为什么默认只是告警：决定「这段内容是不是这张单的」是**哈希 + 唯一 event id + task 标记**，
那三项已经是硬阻断。而一条临时通知（"read your inbox"）没有声明过任务，却会因为它没写出
任务号被拒——那会让门禁变成噪音源。

### 判定为 `FAILED` 而不是 `DEFERRED`

内容不对的单子不会因为等一会儿就变对。用可重试状态会让它每个 tick 重新排队、
重新被拒——一个穿着勤勉外衣的日志洪水。`receipt.signal = "dispatch_refused"`，
诊断表里落到 `unknown → escalate`：**这需要人，不需要重试。**

### 真实 dry-run（`.tools/gate-demo/`，不发送）

干净派工单（`federation.assignment.render` 真实输出）：

```
order    ...-14b1b146
target   qoder   workspace W-0123456789abcdef01234567
task     AEP-GATE-DEMO-1   event EVT-0A35419357B2467E
size     1028 chars   sha256 08ab2f7f4bd4d561
ack      AEP-ACK:EVT-0A35419357B2467E
body:
  | AEP 派工单 / ASSIGNMENT
  | workspace: W-0123456789abcdef01234567
  | task: AEP-GATE-DEMO-1
  | assignee: qoder
  ...
verdict: send   checked: 12   failed: []   warned: []   exit=0
```

把旧任务的 ACK 行追加进去（复现事故形状）：

```
verdict: refuse
reason : content_unchanged
detail : deposited 08ab2f7f4bd4d561, now 916592874d21ae12
failed : ['content_unchanged', 'one_event_id']
exit   : 2
```

两条独立的判据同时命中——就算有人把哈希重新算一遍，`one_event_id` 仍然会拦住它。

### 离线复算

```powershell
aep --coord-root coordination coord-dispatch --order-id <id> --show-body
aep --coord-root coordination coord-dispatch --all --strict
```

`--show-body` 打人工预览（目标 / 工作区 / 任务 / 事件 / 哈希前 16 位 / ACK 行 / 正文前 6 行），
JSON 里是完整的 12 项与 `checked` 标记。任一张单被拒 → exit 2。

## 3. 这套东西**没有**解决什么

说清楚边界，否则下一轮会把「有门禁」当成「已可靠」。

- **不证明对方接单。** 门禁只管「该不该发」，`AGENT_ACK` 仍然只认目标自己的回复。
- **不解决「单子对、会话错」。** 如果订单和 profile 都指向 `qoder_cn`，但窗口里的会话
  不是该发的那一个，门禁看不出来——那要靠会话校验（`session_marker` / `model_marker` /
  `SESSION_NOT_VERIFIED`），本版没有改动它。
- **不含用户确认环节。** 与 7×24 无人值守冲突，所以做成可复算的拒绝，而不是等人点一下。
- **没有在真实事故的那张单上回放过。** 现场订单不在 `coordination/inbox` 里，我手上只有
  形状相同的复现（上面那条 tampered 记录）。
- **紧急插话没有单独门禁。** 它走同一个 `_dispatch`，因此同样受检；但「插话状态机」
  本身（识别运行状态 → 定位插话入口 → 不点停止）仍未补齐。

## 4. 用户 P0 清单的对应进度

| # | 项 | 状态 |
|---|---|---|
| 1 | 派工内容强校验 | **本版完成**（12 项，硬阻断 + 可离线复算） |
| 2 | 窗口与原会话绑定 | **部分完成**：exe 仲裁 + 身份强度上报；`session_fingerprint` 未做 |
| 3 | 发送闭环统一 | 未做 |
| 4 | ACK 识别与结果回读 | 未做（迟到 ACK 回收仍是缺口） |
| 5 | 桌面执行通道统一 | 未做 |
| 6 | 紧急插话状态机 | 未做（但已受同一门禁保护） |

## 5. 回归

| 环境 | 结果 |
|---|---|
| Windows `.tools/agent-runtime` 3.13.9（第二次） | 933 passed, 3 skipped, 113 subtests |
| Windows（第一次） | 932 passed, 1 failed（超时）, 3 skipped |
| WSL anaconda 3.12.7 | 886 passed, 50 skipped |
| 新增测试 | `tests/test_dispatch_gate.py` 17 项、`tests/test_window_identity.py` 7 项 |

第一次全量里 `test_improvement_stage_watcher.py::test_second_instance_fails_before_reconciliation`
报 `subprocess.TimeoutExpired ... after 12 seconds`——它要起 `pwsh` 跑
`scripts/watch-improvements.ps1`，并发下超时。单独跑该文件 **9 passed（78 秒）**，
紧接着的第二次全量 933 passed 零失败。按「只认能复算的证据」的规矩：
这是环境超时，不是本版引入的回归；但也不记成通过，两次都留在账上。
