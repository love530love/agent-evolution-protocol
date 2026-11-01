# ACK 协议与会话指纹（2026-11-01）

> **更正声明（v0.6.15，2026-11-01）**：本文第 0 节与第 4 节的「已发送的单没有 ACK 就 hold」
> **已不再是默认行为**。按用户指令，「没有 ACK 不得推进」从**阻断条件**降级为**记录条件**：
> 默认 `ack_policy="record"` 会推进，并把 `ack.verdict == "hold"` 写进完成回执；
> `ack_policy="enforce"` 才恢复本文描述的行为。**判定逻辑（四道守卫、三值 `match`）
> 一个字都没改**，改的只是「判定结果要不要拦住 DAG」。
> 见 `docs/releases/v0.6.15.md`。
> 本文保留原文，因为它记录的是当时的设计与实测，改写它会让「后来为什么改」失去对照。

两件事，一个共同点：**现在的系统里有太多「看起来成立」的结论**。
「窗口里有那个字符串」被当成「就是这个会话」，「收到一行 ACK」被当成「对方接单了」。
这一版把这两个推断各自拆成可复算的判据。

## 0. 结论

| 问题 | 原来的样子 | 现在 |
|---|---|---|
| 怎么算收到 ACK | 在会话里找 `AEP-ACK:<event>` | 四道守卫的**事件**判定，四道缺一不可 |
| 没有 ACK 能不能推进 DAG | 能，只看 agent 自己写的 `--phase done` | **已发送的单没有 ACK 就 hold** |
| 怎么算「还是那个会话」 | 只看标记字符串在不在 | 会话指纹（agent+exe+title+两个标记） |

## 1. ACK：字符串匹配一定是错的

根因只有一句：**我们要对方回的那个 token，写在我们自己发出去的消息里。**

```
我们发： ... AEP-ACK:EVT-0A35419357B2467E ...
对方回： AEP-ACK:EVT-0A35419357B2467E
```

任何「在会话里找这个字符串」的实现，第一次命中都是**我们自己的请求**。
所以 ACK 不是一次字符串出现，是**一个带角色的事件、发生在正确的会话里、记录在发送之后**。
`coordination/ack_protocol.py` 把这个判断收成一处，四道守卫：

| 守卫 | 挡掉什么 | 拒绝原因 |
|---|---|---|
| `role == "assistant"` | 我们自己那行；用户那行；输入框草稿 | `not_assistant` |
| `direction == "incoming"` | 同一行被当作发出的内容捕获 | `not_incoming` |
| `session_id` 等于订单的 | **另一个会话在回答同一个问题** | `foreign_session` |
| `recorded_at > created_at` | 旧投递的答案（event id 每个任务固定，重发会复用） | `stale` |
| 事件带时间戳 | 无法定位在发送之后的回复 | `no_timestamp` |

拒绝原因分开写，是因为它们要求不同的处置：
「对方还没回」是等；「另一个会话回了」是**消息发错地方了**；
「以 outgoing 出现」是**你在读自己的请求**。合成一个 `False` 就全部看不出来了。

### 没有 timeout 这个状态

`state` 有四个值：`not_sent` / `submitted_unconfirmed` / `sent_unacked` / `acked`。

迟到的回复**必须被接受**。超时是关于我们耐心的陈述，不是关于对方的；
把它当成拒绝，正是重复发送的来源——这一条在 2026-10-31 的实测里已经踩过一次
（应答比 60 秒窗口晚到约一分钟，而消息其实早已送达）。

**第四个值是 v0.6.11 补的，原因是 v0.6.10 说了一句假话。** 当时只有三个值，
而 `CEASED` 被当成「没发出去」。可是 `commander` 在 `CEASED` 里写两种完全相反的 note：

| note 前缀 | 事实 | 原来的判定 | 现在的判定 |
|---|---|---|---|
| `DELIVERY_UNPROVEN:` | 适配器失败，**什么都没打进去** | `not_sent` | `not_sent`（不变） |
| `SUBMISSION_UNCONFIRMED:` | **可能已经发出去了**，不要自动重试 | `not_sent` ← **假话** | `submitted_unconfirmed` |

真实订单抓到的：`2026-10-30T0422243473450000-04d70400`（`qoder` / `AEP-CI-FAIL-20261030`）
最后 6 次尝试的 note 都是 `SUBMISSION_UNCONFIRMED: possible send; do not retry automatically`，
而 v0.6.10 的 `coord-ack` 输出 `state: "not_sent"`。**「可能发过」和「没发」被同一个词说了**，
这在一个以「状态必须为真」为纪律的仓库里是硬伤。

现在多一个字段 `delivery`，三值：`proven` / `unconfirmed` / `none`。
`sent` 仍然只在 `proven` 时为真——**「可能发过」不能把任务钉在等一个也许从没被问过的回复上**，
但也不能被说成「没发」。修后同一张单输出：

```json
"state": "submitted_unconfirmed", "sent": false, "delivery": "unconfirmed"
```

两个 note 前缀与 `commander` 的耦合由 `NotePrefixesAreStillWhatCommanderWritesTests` 守着：
`commander` 改了 note 措辞而这里没跟上，会让判定静默退回「没发」，正是刚修掉的那个缺陷。

### 离线看判定

```powershell
aep --coord-root coordination coord-ack --order-id <id>
```

输出里除了 `state` / `accepted` / `delivery`，还有 `rejected` 列表——每一个被排除的候选
都带自己的原因，而不是只给一个「未确认」。未接受时 exit 2。

真实数据（2026-11-01 只读跑，`coordination/` 里 8 张历史单）：

| 订单 | 结果 |
|---|---|
| `2026-10-30T0345442451860000-7cf3d44f`（qoder_cn，`AGENT_ACK`） | `acked`，2 个候选、1 个被拒 `not_assistant`，exit 0 |
| `2026-10-30T0422243473450000-04d70400`（qoder，`CEASED`） | `submitted_unconfirmed`，exit 2 |

第一张单的 `rejected` 是关键：真实会话里确实有一条带 token 但 `role/direction` 都是 `unknown`
的行（我们自己那句请求），四道守卫在**真实数据**上把它挡下来了，不是只在夹具里成立。

## 2. 没有 ACK 不得推进

`supervisor._settle()` 原来只看 `assignment.complete_evidence()`：agent 自己写
`coord-task-state --phase done` 就算完成，ACK 与否不影响。`CommanderAdapter.acknowledged()`
写好了，但**没有任何调用方**——一个只存在于定义里的检查。

现在的顺序（`_settle` 内）：

```
刷新 ACK（只读，只升级状态，从不打字）
  → agent 报 done？
     → 该单已发送 且 adapter 能报 ACK 且没有 ACK → hold_unacked，任务保持 running，下游不启动
     → 否则 → 正常 complete
```

三条限定，每条都有理由：

1. **只对「已发送」的单生效。** 没发出去的订单没有可确认的对象，等一个没人投递的回复不是严谨，
   是死锁。那种情况下 agent 自己写的证据是唯一可能存在的信号。
2. **只对能报 ACK 的 adapter 生效。** 拿 adapter 给不出的证据去要求它，不是严格，是另一种死锁。
3. **`FAILED` 仍是终态，`CEASED` 仍可被迟到的 ACK 升级。** 这条没动——一次不确定的发送
   不等于永远不能确认，而一次硬失败不该被一条回复掩盖。

hold 会出现在 `supervisor.step()` 返回的 `held` 列表里（同时 `closed` 为空），
所以「为什么没推进」是回执上的一个字段，不是要靠猜。

实测（`tests/test_federation_supervisor.py::AckGateTests`）：

| 场景 | 结果 |
|---|---|
| 已发送、无 ACK、agent 报 done | `held: [{action: hold_unacked, state: sent_unacked}]`，`execute` 仍 `running`，`review` 仍 `pending` |
| 同上，随后 transcript 出现正确 ACK | `closed: [{task: execute, action: complete}]`，`held: []` |
| 未发送、agent 报 done | 不 hold，正常 `done`（没有可确认的对象） |

## 3. 会话指纹

标记检查回答的是「这个字符串在不在屏幕上」。侧边栏里也有这个字符串，另一个实例的会话列表里也有。
所以「标记匹配」和「还是那个会话」是两个问题。

`coordination/session_fingerprint.py` 对**会话身份**做摘要，参与哈希的字段：

```
agent + exe + title + session_marker + model_marker
```

**故意排除 pid。** 重启不是换会话，而一个每次重启就失效的指纹，操作者会学会「无脑重录」——
那等于没有指纹。pid 仍然**记录**在文件里，作为上下文：「同一个会话，现在在 pid 4242」
正是崩溃之后需要的那句话。

本机实测（只读，2026-11-01）：

```
qoder        pid=22796  fp=332555763b7f26a5   exe=D:\Programs\Qoder\.qoder-versions\0.5.1\Qoder.exe
qoder_cn     pid=56368  fp=20eb3eb74f41b52d   exe=D:\Programs\QoderCN\Qoder CN\.qoder-versions\0.5.1\Qoder CN.exe
workbuddy    pid=3560   fp=82dad2ad037bae62   exe=D:\Programs\WorkBuddy\WorkBuddy.exe
```

`qoder` 与 `qoder_cn` 指纹不同——两个产品在同一台机器上是可区分的，这是上一版 exe 仲裁的前提，
现在有了独立的一票。

### 它抓什么，抓不到什么

| 情况 | 能否发现 |
|---|---|
| 同一会话重启（pid 变了） | **不算变化**（这是设计） |
| 装到了另一个目录 / 另一个产品 | 能（`exe` 进 `diff`） |
| profile 被指向另一个会话 | 能（`session_marker` 进 `diff`） |
| 模型切换 | 能（`model_marker` 进 `diff`） |
| **同一 profile、窗口现在显示的是另一个会话** | **不能** |

最后一行必须说清楚：指纹里的两个标记来自 **profile**，不是从屏幕读的。
「窗口里现在是不是那个会话」仍然是 `ui_fallback` 的 `SESSION_NOT_VERIFIED` 在管，
本版没有合并这两件事。把指纹说成能解决会话错配，是这一版最容易犯的过度声称。

### 记录与比对

```powershell
aep --coord-root coordination coord-client fingerprint --agent qoder --show   # 只看，不写
aep --coord-root coordination coord-client fingerprint --agent qoder          # 记录
```

没有记录时 `compare().match` 是 **`None`**，不是 `True`。
「没人记录过指纹」和「指纹一致」是两件事，把它们合成一件，检查就变成了装饰。
派发路径遇到 `match is None` 会照常发送，并把 `session_fingerprint` 原样写进
`WINDOW_FOUND` 回执；遇到 `match is False` 则 `DEFERRED` + `SESSION_FINGERPRINT_MISMATCH`，
`diff` 里是具体哪个字段不同。

## 4. 没做的

- **会话内容不在指纹里。** 见上表最后一行。
- **hold 没有上限。** 一个永远不 ACK 的 agent 会让任务永久 `running`。这是有意的
  （无人值守不该在未验证的工作上宣布胜利），但也就意味着**需要一个「放弃并标记 blocked」的策略**，
  本版没写。
- **`ack_tracker.py` 没有合并。** 它是另一套基于租约的 ACK 记录（`clients/acks/`），
  和本协议并存。合并它们要动 wake 链路，本版没碰——但两套并存正是这个仓库一直警告的事，
  记在这里而不是假装统一了。
- **`provider_readback` 的 `aep-readback-v1` 仍是独立契约。** ACK 判定现在归 `ack_protocol`，
  但 readback 回执格式没改，避免同时动两个面。

## 5. 回归

见 `docs/releases/v0.6.10.md`。新增测试：

- `tests/test_ack_protocol.py`：13 项（四道守卫各一条、迟到接受、无 timeout、拒绝原因保留）
- `tests/test_session_fingerprint.py`：9 项（重启不变、换目录/换会话/换模型都变、无记录为 `None`）
- `tests/test_federation_supervisor.py::AckGateTests`：3 项
- `tests/test_dispatch_gate.py::FingerprintGateTests`：2 项
