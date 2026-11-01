# 两套 ACK 能不能合并（2026-11-01）

从 v0.6.10 起，每个版本的「没做的」里都有同一句：`ack_tracker.py` 未合并。
本文只做一件事：把它拆到能动手或能结案的程度。

**结论先说**：两套方案记的不是同一件事；但对同一张真实的单，它们已经给出三个互相矛盾的回答（§2.1）。
这不是「谁更严格」的问题，而是同一个词被用来指五件事——磁盘与代码里把 `ack` 写进状态字符串的地方
有五个，其中只有一个回答的是「对方接单了」，另有三个用同一个词回答的是别的问题
（§3 表格末三行：`wake_router`、`session_registry`、`team_dispatch`）。

本文全部数字都来自 2026-11-01 对 `coordination/` 的只读扫描；凡未运行的，标注「未验证」。

---

## 0. 一页对照

| | 租约式 `ack_tracker.py` | 事件式 `ack_protocol.py` + `commander` |
|---|---|---|
| 记录位置 | `<root>/clients/acks/<event_id>.json`（`ack_tracker.py:15`） | `<root>/commander/inbox|state/<order_id>.json`（`commander.py:181,229`） |
| 一行的键 | `event_id, agent, state, opened_at, lease_seconds`（+`ack_at`,`detail`） | 整张订单：`id,event_id,target,task,state,created_at,attempts[],ack` |
| 主键 | `event_id`，**一个 event 只允许一行** | `order_id`，**一个 event 允许多张单、多次尝试** |
| 谁写 | 调用方进程自己（`open_request` / `acknowledge`） | `commander._record_attempt`，由派发链写 |
| 状态词表 | `ack_pending / acknowledged / ack_timeout / unknown` | `acked / sent_unacked / submitted_unconfirmed / not_sent / abandoned` + 订单阶梯 10 态 |
| 有没有 `timeout` | **有**，而且一次零超时读就会写进去（`:56-58`） | **故意没有**（`ack_protocol.py:17-20`） |
| 证据要求 | 无。调用方说 `acknowledge(agent=...)` 就算（`:28-38`） | 四道守卫：role / direction / session_id / recorded_at（`:161-177`） |
| 能回答 | 「这个 event 的等待结束了没有」 | 「这个 agent 在**这个会话**里回没回**这句话**」 |
| 磁盘上实有行数 | **0**（`coordination/clients/acks/` 不存在） | 24 张单 |

---

## 1. 两套方案实际记了什么

### 1.1 租约式：一行 = 一次等待

代码只有 61 行，全部语义在 `ack_tracker.py`。临时目录实跑得到的行（非仓库内）：

```json
{"event_id":"EVT-DEMO","agent":"qoder","state":"ack_pending",
 "opened_at":"2026-11-01T10:18:04.130696+00:00","lease_seconds":300}
```

路径是 `clients/acks/EVT-DEMO.json`——**扁平、以 event_id 为文件名、没有 agent 子目录**。

关键行为三条，都读自代码：

1. `open_request` 在 `state ∈ {ack_pending, acknowledged}` 时抛 `ValueError("duplicate event_id")`（`:20-21`）。
   即：**同一 event 不能有两个活着的请求**。
2. `wait_for_ack(timeout_seconds=0)` 是「非阻塞读」，但它**写盘**——把行改成 `ack_timeout`（`:55-58`）。
   实测：`open_request` 后立刻零超时读，返回 `state: ack_timeout`，磁盘上的行也变成了 `ack_timeout`。
3. `acknowledge` 只校验 `row["agent"] == agent`（`:33-34`）。没有会话、没有角色、没有时间先后。

**它记录的是「等待」这一事实，不是「回复」这一事实。**

### 1.2 事件式：一行 = 一张订单的全部历史

订单行（`commander.submit_order`，`:159-167`）带 `attempts[]`，每次派发追加一条
`{at, state, transport, receipt, note}`（`_record_attempt`，`:206-231`）；`state` 是十态阶梯
（`:49`：`DEPOSITED → WINDOW_FOUND → INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK`，
外加 `DEFERRED/CEASED/FAILED/RETIRED/ABANDONED`）。

`ack_protocol.verdict()` 不读 `attempts` 里的状态，只读两样：**订单的 `event_id`/`session_id`/`created_at`**，
和 **`transcript_bus` 里该 agent 的事件**。四道守卫缺一不可（`:161-177`）。

它记录的是「一个带角色的回复事件」，并且**故意不记录超时**（`:17-20`）：
「超时是关于我们耐心的陈述，不是关于对方的」。

### 1.3 磁盘上真实存在的，和文档说的不是一回事

这一条必须先说清楚，否则后面全是错的。

| 目录 | 谁写 | 行数 | 内容 |
|---|---|---|---|
| `coordination/clients/acks/` | `ack_tracker` | **目录不存在** | — |
| `coordination/acks/workbuddy/` | `cli.coord_ack_message`(`cli.py:653`)、`coord_ack_topic`(`:607`)、`bridge.py:238,312` | **4** | 邮箱已读回执，不是 `ack_tracker` 写的 |
| `coordination/commander/inbox/` | `commander` | **24** | 订单 |
| `coordination/transcript_bus/events/` | `transcript_bus` | **16** | 会话事件 |
| `coordination/delegation/receipts/` | `team_dispatch` | **27** | 派发回执，7 条 `AGENT_ACK` |
| `coordination/wake_ack/` | `bridge.clear_wake_pending`(`:165`) | **目录不存在** | — |

四个 `coordination/acks/workbuddy/*.json` 的形状是
`{"agent":"workbuddy","at":"2026-10-28T13:55:3x","batch":true,"id":"...","topic":"..."}`——
**没有 `state`、没有 `lease_seconds`、没有 `opened_at`**，与 `ack_tracker` 的行结构没有交集。
四个文件的时间戳全部落在 `2026-10-28T13:55:35Z`–`13:55:39Z` 四秒内，`batch: true`，
是一次批量扫读留下的。

**所以：租约式方案在生产数据上是零行。** 它唯一的语义调用方是
`resilience_scenarios.py:7,17-21` 的 `ack_timeout` 混沌场景，其余只有两个测试
（`tests/test_ack_tracker.py`、`tests/test_conflict_guards.py:17-22`）。

---

## 2. 它们在哪里对同一件事说了不同的话

这是本文的核心。下面每一条都有真实文件。

### 2.1 一张单，三种答案（`2026-10-13T161857891017+0000-78b0cbea`）

| 判据 | 输出 | 依据 |
|---|---|---|
| 邮箱回执（`acks/`） | **ACKNOWLEDGED** | `acks/workbuddy/20261013T161857373974Z-2165bc0c.json` 存在，`at=2026-10-28T13:55:35Z` |
| 订单阶梯（`commander/state`） | **FAILED**，`ack: null` | 9 次尝试：1×`WINDOW_NOT_UNIQUE: 2 windows; refusing to guess`，8×`EXPIRED` |
| `ack_protocol.verdict`（实跑） | **not_sent**，`delivery: none` | `commander.ack_state()` 只读调用结果 |

三者都是真的，指的是三件事：
邮箱里那封信被批量标记为已读；桌面上一个字都没打进去（窗口不唯一，适配器拒绝猜）；
因此没有任何会话事件可以承载回复。**但三个地方都用了 `ack` 这个词。**

更糟的是第四层：**这张单即使真的收到完美回复，判定仍然是 `not_sent`。** 实测：

```
订单无 session_id  →  verdict = not_sent        （classify 判 foreign_session）
把 session_id 填成 64928 → verdict = acked      （其余不变）
```

根因在 `ack_protocol.py:167-169`：`if not session_id or row["session_id"] != session_id`
——订单缺 `session_id` 时**一律判 foreign_session**。24 张单里 **6 张没有 `session_id`**
（全部是 2026-10-13 那一批，早于会话指纹）。对这 6 张，事件式方案的 `acked` 是**不可达状态**。

### 2.2 方向相反的不一致：4 条 vs 1 条

`delivery_audit` 只读跑（`coordination/`，43 条消息）：

```
messages 43 | mailbox_read 4 | message_visible 1 | agent_accepted 1
desktop_states: NOT_ATTEMPTED 27, CEASED 7, FAILED 5, DEFERRED 3, AGENT_ACK 1
```

| 消息 | `mailbox_read` | `desktop` | `agent_accepted` |
|---|---|---|---|
| `20261013T133008570378Z-87e3a2f8` (workbuddy) | **true** | NOT_ATTEMPTED | false |
| `20261013T134012508381Z-28bb800c` (workbuddy) | **true** | NOT_ATTEMPTED | false |
| `20261013T135425034905Z-ea833a74` (workbuddy) | **true** | NOT_ATTEMPTED | false |
| `20261013T161857373974Z-2165bc0c` (workbuddy) | **true** | FAILED | false |
| `20261030T034543379179Z-f01a1fdf` (qoder_cn) | **false** | AGENT_ACK | **true** |

**系统唯一真正接单的那条消息，没有邮箱回执；四条有邮箱回执的消息，一条都没被接单。**
两个存储在这 43 条消息上完全不重叠。这不是巧合——它们由不同的动作写入，见 §3。

### 2.3 词表冲突：`timeout` 与 `acknowledged`

`ack_protocol.py:17-20` 花了一整段论证「`state` 里不该有 `timeout`」，理由是
超时是我们的耐心，把它当成拒绝正是重复发送的来源。
而 `ack_tracker.wait_for_ack` 恰恰有一个 `ack_timeout`，而且**由一次读取产生**。

这不是「租约式更宽松」，是**两套方案对「等」这件事的定义相反**：
一边认为等待没有终点（只有人签字才能结束，`ABANDONED`），一边认为等一次就是一次超时。

### 2.4 结构性冲突：`event_id` 唯一 vs 重投复用

| 位置 | 对 event_id 的假设 |
|---|---|
| `ack_tracker.py:20` | 同一 event 活着时**不许再开** |
| `ack_protocol.py:14-15` | event id 每任务固定，重投会复用——所以必须有 `stale` 守卫 |
| `commander.py:1059` `resume_deferred` | 「keeping the event ID」，同一 event 反复重投 |
| `wake_ingress.py:129-131` | 崩溃后从 `wake_processing` 重消费，用**同一个** `event_id` 再 `submit_order` |

真实证据：订单 `2026-10-30T0422243473450000-04d70400` 有 **44 次尝试**，全部同一个 `event_id`。

**这两套的唯一性假设不能同时成立。** 这是合并真正的硬约束，不是风格差异。

### 2.5 同一个 token，三种拼法

`ack_protocol.token_for` 要求子串精确等于 `AEP-ACK:<event_id>`（`:73-76`）。
但 `transcript_bus` 里实际出现的是三种：

```
AEP-ACK:E94B7BE8105137                                   （delegation 路径，hex 串）
AEP-ACK: event=AEP-T162-QCN-20261029-01 state=CLAIMED     （agent 自由发挥）
AEP-ACK:20261030T034543379179Z-f01a1fdf                   （唯一能被认出的那种）
```

前两种永远不会被 `classify` 接受——不是 bug，但意味着「回 ACK」这件事在协议外还有别的写法，
而合并方案必须决定认不认它们。

---

## 3. 谁在消费

`grep` 结果，按「合并会不会把它弄坏」排序。

| 消费者 | 读什么 | 位置 | 合并后的风险 |
|---|---|---|---|
| `commander.check_acks` | 订单 + `transcript_bus` | `commander.py:824-855` | 若改为读租约行，会把批量已读升格成 ACK（见 §4） |
| `commander.ack_state` / `coord-ack` | 同上，只读 | `:858-870`、`cli.py:3230` | 无 |
| `federation.supervisor._settle` | `check_acks` + `adapter.ack_verdict` | `supervisor.py:129,193-241` | 直接决定 `held` / `closed` |
| `CommanderAdapter.acknowledged` | `order["state"]=="AGENT_ACK"` | `commander_adapter.py:178-180` | 与 `verdict` 不同源；`ack_verdict`（`:182-192`）才是新接口 |
| `delivery_audit` | `commander/state` + `acks/<to>/<id>.json` | `delivery_audit.py:25,47-48` | `accepted = desktop=="AGENT_ACK" and bool(state["ack"])`，是第三套判据 |
| `cli.unread_messages` | `acks/<who>/<name>.json` 存在与否 | `cli.py:248-261` | 改 key 就改变所有未读计数 |
| `cli.coord_delivery_status` | 同上 | `cli.py:624-629` | 输出字面量 `ACKNOWLEDGED` / `DEPOSITED_UNCONFIRMED` |
| `cli.coord_ack_message` / `coord_ack_topic` | 写 `acks/` | `cli.py:607,653` | 写入方 |
| `bridge.unread` / `bridge ack` / `ack-topic` | 读写 `acks/` | `bridge.py:150,238,312` | 写入方，旧 CLI |
| `hold_alerts.emit` | 写 `messages/<to>/<id>.json` | `hold_alerts.py:145` | **间接**：告警是否算未读由 `acks/` 决定 |
| `wake_router.route` | 自己发 `ACKNOWLEDGED` | `wake_router.py:45-48` | 独立第四套词表 |
| `session_registry` | 状态含 `acknowledged` | `session_registry.py:15` | 独立第五套词表 |
| `team_dispatch.acknowledge` | 写 `delegation/receipts` | `team_dispatch.py:198-213` | 独立存储，7 条 `AGENT_ACK` |
| `resilience_scenarios` | 调 `ack_tracker` | `resilience_scenarios.py:7,17-21` | 删模块即 ImportError |

关于「合并要动 wake 链路」这句话，**我要更正它**：`wake_ingress.py` / `wake_daemon.py` 里
没有任何一处读 `acks/`（`grep -rniw "ack|acks|acknowledged"` 零命中；不带词边界时
只命中 `background`、`fallback` 里的子串）。wake 链路真正的耦合点是
`wake_ingress.py:129-131` **复用 event_id**，也就是 §2.4 那条唯一性冲突，不是读存储。

---

## 4. 合并要付出什么（从小到大）

1. **先记基线，不改代码。** 记录 §2.2 那组数字（43/4/1）与 24 张单的判定分布
   （`acked 1 / sent_unacked 6 / submitted_unconfirmed 2 / not_sent 15 / abandoned 0`）。
   没有基线，后面任何一步都无法判断是修好了还是改坏了。**可逆**。
2. **`wait_for_ack` 的零超时读不再写盘**（`ack_tracker.py:55-58`）。
   一处 4 行改动，但会打破 `tests/test_ack_tracker.py:13` 的断言——测试必须一起改，
   且理由要写进提交信息（读操作不该改事实）。**可逆**。
3. **对齐词表**：`acknowledged` → `acked`。影响 `test_ack_tracker.py:8`。
   或者更省事的做法：保留租约词表，在边界上做映射，不改存储。**可逆**。
4. **给 `acknowledge()` 加证据要求**：现在它接受一个裸调用（`:28-38`）。
   合并意味着它必须接受一个 `verdict`，否则同一个词在两边含义不同。
   今天调用方只有测试，**现在做几乎零成本；等有人接进派发链就变成不可逆**。
5. **把租约存储从 `clients/acks/` 迁到 `commander/`**（`ack_tracker.py:15`）。
   `clients/` 是 `client_registry` 的命名空间（`clients/*.json`、`clients/anchors`、
   `clients/sessions`，见 `client_registry.py:198,218-222`、`session_registry.py:24`），
   一个 ACK 存储寄居在客户端注册表下面，是把两件事混成一个目录。
   **路径迁移本身不可逆**；本仓库要迁的数据是 0 行，所以**现在是最便宜的时刻**。
6. **把 `open_request` 的唯一性改成「追加尝试」**（`:20-21`）。
   **这一步会打断活的投递路径**：`wake_ingress.py:129-131` 崩溃重消费时用同一个 `event_id`，
   `resume_deferred` 明确保留 event id。改错的表现不是报错，是**唤醒事件在重消费时丢失**。
   必须与 5 同批做，并且先补一条「同一 event 两次 submit_order 仍各自可 ACK」的测试。
7. **让 `delivery_audit` 的 `accepted` 改调 `ack_protocol`**（`delivery_audit.py:48`）。
   它会改变现有 `agent_accepted` 的计数口径；因为 §2.1 里那张单缺 `session_id`，
   改完之后可能出现「订单是 AGENT_ACK 但审计说不算」的新组合。**先算，再改**。

---

## 5. 不要做的事

每一条都从上面的证据里来，不是通用告诫。

1. **不要让 `check_acks` 接受邮箱回执或租约行。**
   证据：四条有回执的消息，三条 `desktop=NOT_ATTEMPTED`、一条 `desktop=FAILED`；
   其中 `WINDOW_NOT_UNIQUE` 那次适配器**拒绝打字**。把「读过邮箱」升格成「接单」，
   等于给一封没人打开的信签发收条。
2. **不要在 wake 链路还复用 event_id 的时候给租约加唯一约束。**（§2.4、§4 第 6 步）
3. **不要删 `ack_tracker.py` 而留着 `resilience_scenarios.py:7` 的 import。**
   `ack_timeout` 场景是这个模块唯一的语义调用方，删模块会让混沌场景 ImportError，
   而它是目前唯一覆盖「等不到 ACK」这条路径的东西。
4. **不要把 `wait_for_ack` 的 `ack_timeout` 语义搬进合并后的判定。**
   `ack_protocol.py:17-20` 为「没有 timeout」写了论证，且 2026-10-31 已经踩过一次
   （应答晚到约一分钟而消息早已送达）。搬过去等于把刚删掉的缺陷装回去。
5. **不要顺手把 `coord_delivery_status` 的 `ACKNOWLEDGED` 改成别的词就完事。**
   它同时输出 `execution_proven: false`（`cli.py:629`），这已经是一次诚实的自我限定；
   改词而不改语义只会让外部脚本匹配不到。
6. **不要把 `hold_alerts` 写进 `messages/` 的告警纳入 ACK 判定。**
   告警的 id 由 `sent_at` 派生（`hold_alerts.py:237-245`），一旦 `acks/` 的键规则变化，
   告警可能被算成「已读」而不再出现在未读里——「没人被告知」会变成「没人被告知且看不出来」。
7. **不要在没有新证据的情况下再写一次「已合并」。**
   本文能证明的是：两套方案在真实数据上互不重叠，且各有不可达状态。

---

## 6. 我没能确定的

- **那四条 `batch: true` 回执是谁写的。** `coord-ack-topic` 是给 agent 用的 CLI，
  但四秒内四条、全部 `batch: true`，更像是脚本扫读。仓库里没有调用记录，
  `wake_log.jsonl` 也没有对应条目。要确定需要看 `2026-10-28T13:55` 前后的
  shell 历史或 WorkBuddy 会话原文。**未验证。**
- **workbuddy 那四条消息的内容有没有被模型读到。** `transcript_bus` 里 workbuddy
  只有 **2** 个事件，且 `role`/`direction` 都是 `None` 或 `unknown`，`session_id` 为空。
  也就是说事件式方案在 workbuddy 上**没有任何可判定的输入**——它的 `not_sent` 不是
  「确认没发」，而是「没有证据」。要区分这两者，需要 `coordination/transcript_bus/snapshots/workbuddy.json`
  之外的实时抓取，仓库里没有。**未验证。**
- **`federation/supervisor` 的 `held` 在真实协作区上跑过没有。** 现有 24 张单里
  `abandoned` 为 0，`held` 的历史只在 v0.6.13 的 `test_abandon_demo` 临时目录里。
  要确定需要一次带 `hold_limit_seconds` 的真实 `step()`。**未验证。**
- **`sent_at` 是否等于「发出时刻」。** `verdict` 的 `sent_at` 取的是 `order["created_at"]`
  （`ack_protocol.py:241`），那是入队时刻不是投递时刻。本数据集上二者最大相差 **1.2 秒**
  （`2026-10-30T0345010281910000-6d76e190`），所以现在没有实际影响——
  但队列积压时 `unacked_for_seconds` 会偏大。**这是测量出来的，不是推断。**

---

## 7. 建议

具体做法，按顺序：

1. 让 `ack_protocol` 继续做唯一的**判定**（它已经做对了：`acked 1` 那张单在真实数据上
   挡下了一条 `not_assistant` 的候选）；
2. 让 `ack_tracker` 退化成**等待记账**，不再自己定义 `acknowledged`，只记「谁在等、等了多久、
   谁签的字」——它今天其实只做了这件事，只是用了一个会被误读的名字；
3. 先做 §4 的第 1、2、3 步（全部可逆、无外部影响），把 §4 第 5、6 步留到
   `wake_ingress` 的 event_id 复用问题单独解决之后再做。

不合并也可以，但那样必须**改掉名字**：只要 `acks/`、`AGENT_ACK`、`ACKNOWLEDGED`、
`acknowledged`、`ack_pending` 这五个词还同时出现在磁盘上，
§2.1 那张单就会继续有三种正确答案，而下一个人会再写一份 v0.6.10 里的那句「未合并」。

**建议：部分合并（merge-partially）。最强的一条理由是：两套方案各有一个在真实数据上
「永远到不了的状态」——租约式的 `ack_timeout` 来得毫无证据（任何人调一次 `acknowledge` 就算），
事件式的 `acked` 对 6 张缺 `session_id` 的历史单不可达（实测，即使给它一个完美回复）——
而这两个缺口恰好互补，二选一会删掉其中一个仍然需要的信号。**
