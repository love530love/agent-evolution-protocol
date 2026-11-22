# 配额感知指挥接力（Quota-aware Command Relay）

> 状态：实现并单元/集成测试通过；已做一次真实桌面投递尝试，因目标窗口会话标记
> 不匹配而在输入前失败关闭。成功唤醒与 ACK 仍未验收。
> 本文描述 `relay/resources.py`、`relay/duty.py` 与
> `python -m agent_evolution_protocol.relay` 的现有契约，不把未执行的窗口聚焦、信箱投递或
> 模型自述当作 ACK、交付或额度证明。

## 1. 为什么需要这一层

多厂商 agent 各自有独立的用量、积分与时间上限。如果某项工程只能由某一家
agent 指挥，一旦它撞到额度，整个工程就停摆。目标不是让一个 agent 再开很多
subagent，而是让**角色**（不是厂商会话）在多个 agent 之间可验证地换防：

- 每个 agent 只在自己的额度窗口内工作，并预留一小份 `reserve` 用于交接；
- 当前持有者可以在耗尽前主动发出带检查点的换防邀约；
- 接手者用检查点哈希显式接单，而不是“看到消息”就算接手；
- 旧持有者的心跳不能给新持有者“续命”；
- 所有变更通过 `command_handoff` 的单写者状态机完成，不出现双指挥。

这一层由三块组成：

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 角色状态机 | `federation/command_handoff.py` | 单一 `ACTIVE` 持有者、`YIELDED`/`LIMITED`、接管事件链 |
| 额度信封 | `relay/resources.py` | 每个 agent 的剩余量、预留、重置时间、能力与证据 |
| 值班接力 | `relay/duty.py` | 心跳、检查点、席位判断、换防计划、邀约/接受/拒绝/接管、信箱与唤醒投递 |

## 2. 额度信封

### 2.1 字段与状态

`relay/resources.py` 写入 `<coord-root>/relay/resources/<agent>.json`，schema 为
`aep-quota-envelope-v1`。核心字段：

| 字段 | 含义 |
| --- | --- |
| `provider` | 厂商标识，仅作记录，不调用厂商 API |
| `unit` | `credits` / `requests` / `tokens` / `minutes` / `percent` / `unknown` |
| `limit` | 当前窗口总量，可为空 |
| `remaining` | 当前窗口剩余量，包含 `reserve` |
| `reserve` | 不用于普通工作的预留量 |
| `available` | `max(0, remaining - reserve)`，由代码计算，不信任调用方自报 |
| `reset_at` | 额度重置时间（ISO 8601） |
| `source` | `manual` / `provider_api` / `screenshot` / `hook` / `self_report` |
| `evidence` | 非空证据列表；没有证据不被接受 |
| `capabilities` | 该 agent 可承担的能力 token |
| `subagent` | `max_children` / `max_units`，可选的子 agent 预算 |
| `expires_at` | 信封自身过期时间；默认 60 分钟，必须重新声明 |
| `blocked` | 显式耗尽标记，由 `exhausted` 写入 |

`effective()` 计算以下状态之一：

- `ACTIVE`：未阻塞、未过期且 `available > 0`；
- `EXHAUSTED`：未阻塞、未过期但 `available <= 0`；
- `BLOCKED`：持有者/操作者显式声明普通工作必须停止；
- `EXPIRED`：信封过期，必须重新声明；
- `UNKNOWN`：没有信封；
- `INVALID`：字段类型或时间不可读。

`gate()` 默认 fail-closed：未声明（`UNDECLARED`）、非 `ACTIVE`、可用量不足或
子 agent 预算不足都会拒绝。`rank()` 只按已声明信封排序，不联系人、不唤醒、
不访问厂商 API。

### 2.2 示例

```bash
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination declare \
  --agent workbuddy --provider workbuddy --unit credits \
  --remaining 80 --reserve 15 --limit 100 \
  --capabilities command desktop-ui \
  --subagent-children 2 --subagent-units 10 \
  --evidence "operator screenshot 2026-11-22 09:00" --source screenshot
```

> 额度是**自述证据**，不是厂商余额证明，也不是 ACK、唤醒或执行凭据。
> 这一层没有额度 watcher，不自动读取任何厂商页面。

## 3. 心跳、检查点与席位

### 3.1 心跳不是 ACK

`beat()` 只允许当前 `ACTIVE` 或 `LIMITED` 持有者调用。它写入：

- `<coord-root>/relay/heartbeats/<role>/<agent>.json`：心跳本身；
- `<coord-root>/relay/packs/<sha256>.json`：规范化后的检查点包。

检查点至少要有 `next_step`，并且 `completed` / `artifacts` /
`pending_operations` / `unknowns` 至少一项非空；否则返回
`CHECKPOINT_TOO_THIN`。检查点哈希由规范化 JSON 计算，可用于接受时校验。

心跳默认 15 分钟有效，最大 240 分钟。它是 agent 的自述，不是：
- 目标 agent 收到消息的 ACK；
- 任务执行成功；
- 额度仍然充足；
- 桌面窗口已经被唤醒。

### 3.2 tenure（任期）隔离

每次角色换防都会产生新的 `handoff_id`。`_tenure()` 取“最近一次把当前持有者
装进角色的 handoff”，如果是初始 `claim` 则为 `"claim"`。心跳记录自己的
`tenure`；`heartbeat_status()` 若发现心跳来自旧任期，返回 `STALE_TENURE`，
不把旧心跳当成新持有者的 READY 证据。

这意味着：**接受换防不会伪造心跳**。新持有者必须自己 `beat` 一次。

### 3.3 席位状态

`seat_status()` / `plan()` 会综合角色状态、心跳和额度，给出：

| 席位状态 | 含义 |
| --- | --- |
| `READY` | `ACTIVE` + 心跳 FRESH + 额度 ACTIVE |
| `READY_UNVERIFIED_QUOTA` | `ACTIVE` + 心跳 FRESH + 未声明额度 |
| `QUOTA_BLOCKED` | `ACTIVE` + 心跳 FRESH + 额度 BLOCKED/EXHAUSTED/EXPIRED/INVALID |
| `HEARTBEAT_LOST` | `ACTIVE` + 心跳 STALE/MISSING/INVALID/STALE_TENURE |
| `YIELDED` | 旧持有者已硬让位，可被接管 |
| `LIMITED` | 预警降级，仍可被接管 |
| `TRANSITIONING` | `TAKING_OVER` 中间态 |
| `VACANT` | `STANDBY`，没有持有者 |
| `UNKNOWN` | 无法识别的状态 |

## 4. 换防计划（只读）

`plan()` 不写状态、不发消息、不唤醒，只给出建议动作：

| 动作 | 触发条件 | 建议下一步 |
| --- | --- | --- |
| `HOLD` | 持有者 READY | 继续工作 |
| `HOLD_QUOTA_UNVERIFIED` | 心跳正常但额度未声明 | 先 `declare`，不要假设额度充足 |
| `PREPARE_OFFER` | 持有者 ACTIVE 但额度受限，且有可报价候选 | 持有者发 `offer` |
| `TAKEOVER_VACANT` | 角色 YIELDED/LIMITED，且有可接管候选 | 候选直接 `takeover` |
| `CLAIM_ROLE` | 角色 STANDBY，且有可报价候选 | 候选 `claim` |
| `RECRUIT_CANDIDATE` | 没有候选，但 `rank()` 发现可招募 agent | 先邀请它 `volunteer` |
| `ALERT_AND_RECRUIT` | 没有候选也没有可招募项 | 通知上游/人工补充 |
| `TRANSITIONING` | 正在接管 | 等待状态机完成 |
| `NO_CANDIDATE` | 空缺且无人可接 | 保持空缺，不伪造 holder |
| `EMERGENCY_REQUIRES_UPSTREAM` | ACTIVE 持有者心跳丢失 | 当前状态机没有 `no_heartbeat` 让位路径，必须上游处理或由持有者显式让位 |
| `UNKNOWN` | 无法判断 | 人工核查，不要猜测 |

`recommended` 只在 `PREPARE_OFFER`、`TAKEOVER_VACANT`、`CLAIM_ROLE` 时出现，
避免在只需 `HOLD` 时诱导无谓换防。

### 4.1 已知上游缺口

`command_handoff.yield_role()` 目前只接受容量信号
（`quota_exhausted` / `credit_exhausted` / `usage_limit` / `time_limit` /
`credit_warning`），没有 `no_heartbeat` 或“失联”让位信号。因此：

- 持有者失联时，`plan()` 返回 `EMERGENCY_REQUIRES_UPSTREAM`；
- 本层**不会**伪造一个 `time_limit` 去绕过状态机；
- 要真正实现自动接管失联席位，需要在 `command_handoff` 增加受控的失联让位
  路径，或在人工介入下由持有者显式 `yield`。

## 5. 邀约、接受、拒绝与空缺接管

### 5.1 正常换防时序

```text
持有者 A 心跳 FRESH
  -> plan() 发现额度受限 + 候选 B
  -> A: offer(role, to=B, signal, reason, checkpoint)
       写入不可变 offer，检查点 pack，信箱投递，必要时 wake 入队
       —— 角色仍属于 A
  -> B: accept(role, offer_id, checkpoint_sha256, evidence)
       校验 offer、检查点哈希、expiry、decline、B 的额度
       -> command_handoff.yield_role(A, signal)
       -> command_handoff.takeover(B)
       -> 写 acceptance 回执，通知 A
       —— 角色属于 B，但 B 还没有心跳
  -> B: beat(role, checkpoint, evidence)
       —— 新 tenure 的心跳 FRESH
```

### 5.2 约束

- `offer` 仅当前持有者可发，且要求持有者心跳 FRESH；
- 目标必须是已登记候选、能力匹配、额度 `ACTIVE` 且 `available >= 1`；
- `offer_id` 由角色、双方、信号、原因、证据、检查点 SHA 与 nonce 规范化推导，
  重复调用不会重复投递；
- `accept` 仅目标 agent 本人可调用，必须带正确 `checkpoint_sha256`；
- `accept` 在角色锁内再次检查 decline 和 expiry，避免“先接受后拒绝”的竞态；
- `accept` 成功后如果投递旧持有者失败，返回 `DELIVERY_FAILED`，但不把换防
  回滚成“未接受”；
- `decline` 只写拒绝标记并通知旧持有者，角色不变；
- `takeover_vacant` 只允许 `YIELDED`/`LIMITED`；对 `LIMITED` 如果旧持有者
  心跳仍 FRESH 且额度 ACTIVE，会拒绝并提示改用 `offer`；
- 所有写路径都要求非空证据；空证据、非法角色、非法信号一律拒绝。

### 5.3 幂等与恢复

- 同一 `offer` 重放返回 `idempotent: true`、`no_redelivery: true`；
- 已写 acceptance 的重放会报告 `current_holder` / `still_holder`，不重复投递；
- 接管后发现当前持有者已经是自己，会通过 `relay-accept:<offer_id>` 证据
  识别“上一次已成功但未写回执”的恢复路径；
- 不通过修改 JSON 状态文件来“修复”冲突；`chain_valid=false` 时停止换防。

## 6. 真实手脚：信箱、唤醒与 --dispatch

### 6.1 投递位置

`offer` / `accept` / `decline` / `takeover` 会尝试投递：

- 信箱：`<bridge-root>/messages/<target>/<event-id>.json`；
- 唤醒队列：`<bridge-root>/wake_queue/<event-id>.json`。

默认 `bridge-root` 等于 coord root（本仓库为 `coordination`）。只有发现以下任一
文件时才写唤醒队列：

- 包内 `coordination/wake_adapters/<target>.ps1`；
- `<coord-root>/ui_profiles/<target>.json`。

否则只投信箱，并返回 `needs_adapter_or_manual_relay`。

### 6.2 `--dispatch`

加 `--dispatch` 时，投递函数会在信箱和唤醒队列就绪后**最多调用一次**
`coordination.wake_ingress.dispatch_wake(bridge, event_id, apply=True)`。

- 没有 adapter 时返回 `SKIPPED_NO_ADAPTER`，不假装唤醒成功；
- 桌面适配器需要 PowerShell 7。若当前进程的 `PATH` 找不到 `pwsh.exe`，relay 会在
  分发期间临时把标准安装目录 `%ProgramFiles%\PowerShell\7` 放到 `PATH` 前面；
  仍找不到时返回 `SKIPPED_PWSH_MISSING`，**不会回落到 PowerShell 5.1**，也不会
  尝试输入。信箱与唤醒队列仍然保留；
- 调用异常返回 `DISPATCH_FAILED_OR_UNKNOWN`，不回滚角色状态，也不盲目重试；
- 失败或未知结果必须由人核查 `wake_queue` 与目标会话；
- 唤醒执行、窗口聚焦、消息可见都不是目标 ACK，更不是接受。

### 6.3 本机真实投递尝试（2026-11-22）

- 只读 `coord-uia observe --agent qoder` 在默认 `PATH` 下回落到 PowerShell 5.1，
  因 UTF-8 脚本被按 ANSI 解析而失败；把 `C:\Program Files\PowerShell\7` 加入
  `PATH` 后同一命令成功，读到 `activity_state=busy` 且插话控件可用。
- 随后对 qoder 执行一次带事件 ID 的 `coord-uia deliver --urgent-interject`。
  适配器在输入前拒绝：`SESSION_MARKER_MISSING`，因为当时打开的 Qoder 窗口是
  另一个“Agent Chat 调研”会话，不是 profile 绑定的协作会话。
- 该结果证明会话围栏会在错误会话前阻断输入，**不证明**唤醒成功、消息可见或
  目标 ACK。按“不确定输入禁止换路由重发”的规则，没有改用信箱或其它窗口补发
  同一条插话。

### 6.4 证据级别

| 证据 | 能证明 | 不能证明 |
| --- | --- | --- |
| 信箱文件存在 | 本地写入了消息 | 目标读过、理解或接单 |
| wake_queue 文件存在 | 已排入唤醒队列 | 适配器执行、窗口聚焦或模型已运行 |
| `dispatch_state` 返回 | 尝试过一次分发 | 目标会话收到或任务完成 |
| `AGENT_ACK` | 目标声明收到 | 任务执行结果 |
| `TASK_RESULT_READBACK` | 结果被回读 | 独立验证通过 |
| `accept` 回执 | 目标用检查点显式接单并完成状态迁移 | 新持有者已有心跳 |

本层没有“唤醒成功 = 已接手”的捷径。

## 7. CLI 实战配方（AI 算子编译大赛四角色）

> 当前入口是 `relay` 包自带的模块命令；`aep` 主命令面由另一个进行中的
> 写入 claim 持有，尚未注册 `coord-command-relay`。等主 CLI 所有者合并后，
> 可以把下面的 `... -m agent_evolution_protocol.relay` 缩写为
> `aep coord-command-relay`，并同步 `skill-manifest.json` 的命令计数。
> 在合并前不要声称 `aep coord-command-relay` 已可用。

以下把角色当功能名，不绑定厂商：

| 角色 | 必需能力 | 示例承担者 | 职责 |
| --- | --- | --- | --- |
| `commander` | `command` | workbuddy | 调度、进度、换防决策 |
| `scout` | `recon` | codex | 资料收集与情报整理 |
| `github` | `repo_intel` | copilot-github | GitHub 内容/仓库情报 |
| `builder` | `build` | qoder | 赛事包制作、上传、回表 |

### 7.1 声明额度并登记角色

```bash
# workbuddy: 指挥
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination declare \
  --agent workbuddy --provider workbuddy --unit credits \
  --remaining 80 --reserve 15 --limit 100 \
  --capabilities command desktop-ui \
  --evidence "initial quota screenshot" --source screenshot

# codex: 侦察
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination declare \
  --agent codex --provider openai --unit requests \
  --remaining 120 --reserve 20 --limit 150 \
  --capabilities recon cli \
  --evidence "usage dashboard 2026-11-22" --source provider_api

# copilot-github: 仓库情报
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination declare \
  --agent copilot-github --provider github --unit requests \
  --remaining 200 --reserve 20 --limit 250 \
  --capabilities repo_intel git-access cli \
  --evidence "monthly quota note" --source manual

# qoder: 构建/交付
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination declare \
  --agent qoder --provider qoder --unit credits \
  --remaining 90 --reserve 15 --limit 120 \
  --capabilities build packaging cli \
  --evidence "client quota page" --source screenshot

.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination register \
  --role commander --capability command
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination register \
  --role scout --capability recon
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination register \
  --role github --capability repo_intel
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination register \
  --role builder --capability build
```

### 7.2 建立指挥并写入第一份检查点

```bash
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination claim \
  --agent workbuddy --role commander --capability command \
  --evidence "kickoff meeting; workbuddy owns dispatch"

.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination beat \
  --agent workbuddy --role commander \
  --evidence "kickoff checkpoint" \
  --next-step "确认四角色候选与赛事截止时间" \
  --completed "角色表已建立" \
  --artifacts "docs/relay/QUOTA_RELAY.zh-CN.md"
```

### 7.3 候选报名

```bash
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination volunteer \
  --agent codex --role commander --capability command \
  --reason "workbuddy 额度受限时接管指挥" \
  --evidence "codex 原会话可接管; 已有一次 AEP 长任务闭环经验"
```

能力不匹配的报名会记录为 `eligible=false`，不会成为可报价候选。

### 7.4 到期/受限时查看计划

```bash
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination plan --role commander
```

或直接查看全局：

```bash
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination status
```

计划输出至少包含：`action_required`、`recommended`、`seat`、`candidates`、
`recruitable`、`blocked_by`。

### 7.5 发起换防邀约

```bash
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination offer \
  --agent workbuddy --role commander \
  --to codex \
  --signal usage_limit \
  --reason "workbuddy 用量接近上限，移交指挥给 codex" \
  --evidence "usage dashboard 92%" \
  --next-step "继续跟进赛事包与回表" \
  --completed "角色表、候选人清单" \
  --unknowns "赛事截止时间以官网为准" \
  --bridge-root coordination
```

默认只投信箱/唤醒队列；需要尝试真实唤醒时再加 `--dispatch`，且必须先确认目标
适配器存在。**不要**在结果不明时自动重复 `--dispatch`。

### 7.6 接手者接受并立即写心跳

```bash
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination accept \
  --agent codex --role commander \
  --offer-id <OFFER_ID> \
  --checkpoint-sha256 <SHA256> \
  --evidence "codex 本人确认接手" \
  --bridge-root coordination

.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination beat \
  --agent codex --role commander \
  --evidence "接管后第一份心跳" \
  --next-step "核对在途任务并给 builder 派工" \
  --completed "已接受 commander 角色" \
  --unknowns "workbuddy 尚未回的 ACK"
```

`accept` 输出里的 `heartbeat` 仍可能是 `MISSING`，这是正确的：接受不等于心跳。

### 7.7 持有者撞额度后的让位/接管

```bash
# 持有者自己声明耗尽（或由操作者按证据调用）
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination exhausted \
  --agent workbuddy --unit credits --remaining 0 \
  --reason "provider hard limit" \
  --evidence "HTTP 429 screenshot"

# 让角色进入 YIELDED/LIMITED
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination yield \
  --agent workbuddy --role commander \
  --signal quota_exhausted \
  --evidence "HTTP 429 screenshot"

# 合格候选直接接管空缺
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination takeover \
  --agent codex --role commander \
  --evidence "workbuddy 已确认停止派工" \
  --next-step "继续推进赛事包" \
  --completed "已读取 workbuddy 检查点"
```

### 7.8 拒绝邀约

```bash
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay --coord-root coordination decline \
  --agent codex --role commander --offer-id <OFFER_ID> \
  --reason "当前只剩 8% 额度，无法承担指挥"
```

角色不改变，旧持有者会收到通知；这也不是 ACK。

## 8. 常见错误与处置

| 输出 | 含义 | 处置 |
| --- | --- | --- |
| `HOLDER_HEARTBEAT_NOT_FRESH:*` | 持有者心跳不新鲜，不能发报价 | 先 `beat` 或改用 `takeover`（仅 YIELDED/LIMITED） |
| `NO_ELIGIBLE_CANDIDATE` | 没有能力+额度都合格的候选 | 先 `volunteer` 或补额度声明 |
| `TARGET_QUOTA_NOT_ELIGIBLE:*` | 指定候选额度不满足 | 让它重新 `declare`，不要伪造 |
| `CHECKPOINT_MISMATCH` | 接受的检查点 SHA 不对 | 读取 offer 上的正确 SHA，不要猜 |
| `OFFER_EXPIRED` | 邀约已过期 | 由持有者重新发一份新 offer |
| `STALE_TENURE` | 心跳属于旧任期 | 新持有者必须重新 `beat` |
| `HOLDER_STILL_LIVE_USE_OFFER` | LIMITED 旧持有者仍有心跳和额度 | 用 `offer` 换防，不要硬抢 |
| `EMERGENCY_REQUIRES_UPSTREAM` | 失联持有者没有可用的让位路径 | 人工介入或上游增加失联让位信号 |
| `DELIVERY_FAILED` | 状态已变但通知/投递失败 | 人工核查信箱/唤醒队列，不要把换防回滚成未发生 |
| `DISPATCH_FAILED_OR_UNKNOWN` | 唤醒结果不确定 | 先读队列和目标会话，禁止盲目重发 |

不要通过编辑 `coordination/relay/*.json` 或 `command_handoff/state.json`
“修复”错误。状态机与哈希链会拒绝不一致的数据。

## 9. 文件布局

```text
<coord-root>/
  command_handoff/state.json        # 角色状态机与哈希链
  relay/
    resources/<agent>.json          # 额度信封
    heartbeats/<role>/<agent>.json  # 心跳（含 tenure）
    packs/<sha256>.json             # 检查点包
    offers/<offer_id>.json          # 不可变邀约
    acceptances/<offer_id>.json     # 接受回执
    declines/<offer_id>.json        # 拒绝标记
    takeovers/<handoff_id>.json     # 空缺接管回执
    locks/<role>.lock               # 每角色写锁
  messages/<agent>/<event>.json     # 信箱
  wake_queue/<event>.json           # 唤醒请求（可选）
```

所有文件写入使用同目录临时文件 + `os.replace`，避免半截 JSON。

## 10. 验收命令

```bash
.tools/agent-runtime/Scripts/python.exe -m pytest -q \
  tests/test_relay_resources.py tests/test_relay_duty.py
```

当前本地结果：29 项通过。还应跑上游并存的：

```bash
.tools/agent-runtime/Scripts/python.exe -m pytest -q \
  tests/test_command_handoff.py tests/test_collab_health.py \
  tests/test_presence_robustness.py tests/test_supervisor_registration.py \
  tests/test_foundation.py
```

若有外部 watcher/mutex 相关的已知失败，必须单独说明，不把失败说成通过。

## 11. 本地模型（`.models/Qwen3-0.6B`）的受控用途

`relay/local_brief.py` 提供了一个显式的、只读的“检查点摘要”适配器：

- 默认模型 `qwen3-0.6b`，默认端点 `http://127.0.0.1:1234`（LM Studio）；
- 它只接收 `next_step` / `completed` / `artifacts` / `pending_operations` /
  `unknowns`，输出 `summary` / `next_step` / `risks` 三段建议文本；
- 输出会做长度和形状校验，含 `unknown:true`、非法 JSON 或超限内容时直接
  `ABSTAINED`；
- 模型文件缺失或服务不可达时返回 `UNAVAILABLE`，**不会启动服务**、不会加载
  常驻模型，也不会阻塞确定性 relay 路径；
- 它不改变角色状态、不选候选、不写 ACK、不授权上传，也不是执行凭据。

用法：

```bash
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.relay \
  --coord-root coordination brief \
  --next-step "继续跟进赛事包与回表" \
  --completed "角色表已建立" \
  --artifacts "docs/relay/QUOTA_RELAY.zh-CN.md" \
  --unknowns "官网截止时间"
```

这一步只读取 `.models/Qwen3-0.6B/model.safetensors` 是否存在，并尝试调用
**已经在运行**的 LM Studio。当前本机 127.0.0.1:1234 未启动，尚未完成真实
推理验收；因此只能说“适配器已实现、单测通过”，不能说“本地模型已接管”。
任何“本地模型已接管/已执行”的说法都不成立。

## 12. 尚未完成的真实落地项

1. **自动化额度采集**：没有厂商 API/watcher，额度仍由人或 agent 显式声明。
2. **失联自动让位**：`command_handoff` 没有 `no_heartbeat` 让位信号，当前只能
   `EMERGENCY_REQUIRES_UPSTREAM`。
3. **真实唤醒验收**：`--dispatch` 代码路径已存在；已执行一次真实插话尝试，
   但目标窗口会话标记不匹配，输入前失败关闭。尚未完成一次“正确会话内产生
   `MESSAGE_VISIBLE` 并收到目标 ACK”的端到端验收；窗口聚焦/消息可见不能替代 ACK。
4. **身份与签名**：agent 名是自述字符串，offer/acceptance 没有签名或外部锚；
   单机文件锁只解决并发，不解决投毒或伪造。
5. **赛事端到端演练**：四角色映射尚未在真实赛事中完整跑过一轮跨厂商换防。
6. **跨机共享**：默认假设 coord root 与 bridge root 在同一文件系统；跨机需要
   明确的共享存储/传输层，当前未实现。

## 13. 设计红线

- 不把“窗口聚焦、信箱投递、唤醒执行”当作 ACK；
- 不把“额度声明”当作厂商余额证明；
- 不让旧 tenure 的心跳给新持有者续命；
- 不在没有检查点哈希的情况下接受换防；
- 不在没有证据的情况下让位、接管或宣告耗尽；
- 不在结果不明时切换通道重发；
- 不新增默认的新会话；唤醒策略保持 `forbid-by-default`。

## 14. 交接清单（给下一任 relay 维护者）

### 14.1 归属与验证

- claim：`AEP-QUOTA-RELAY-20261122`，负责
  `src/agent_evolution_protocol/relay/`、`tests/test_relay_duty.py`、
  `tests/test_relay_resources.py`、`docs/relay/`。
- 当前改动以 `git log --all --oneline -- src/agent_evolution_protocol/relay` 为准；
  2026-11-22 的 relay 提交包括 `731892d`、`f1ad9f2`、`cc3cb4e`、`ca40d7c`，
  以及仅通过 CLI 驱动换防的测试提交。
- 定向回归：

  ```bash
  .tools/agent-runtime/Scripts/python.exe -m pytest \
    tests/test_relay_duty.py tests/test_relay_resources.py \
    tests/test_command_handoff.py tests/test_collab_health.py \
    tests/test_presence_robustness.py tests/test_supervisor_registration.py \
    tests/test_foundation.py -q
  ```

  当前为 92 passed。全量套件另有 `tests/test_legion_ledger.py` 的 3 个既有
  PowerShell 输出解码失败，与 relay 无关，未在本 claim 内修改。

### 14.2 下一步优先级

1. **并入主命令面**：主 `cli.py` 由 `AEP-COALITION-SUCCESSION-20261122` 持有。
   该 claim 释放后，把 `relay.cli.build_parser()` 的 action 注册为
   `aep coord-command-relay`，并同步 `skill-manifest.json` 与本文的 CLI 示例。
2. **共享 PowerShell 7 解析器**：`cli.py::_pwsh`、
   `commander_uia.py::_pwsh`、`ui_fallback.PowerShellUiaDriver`、
   `wake_daemon.py`、`transcript_patrol.py` 目前都只查
   `shutil.which("pwsh.exe")`。应统一为“先 which，再查
   `%ProgramFiles%\PowerShell\7\pwsh.exe`；找不到就失败关闭”，避免静默回落
   5.1 后把 UTF-8 脚本解析坏。relay 侧已有 scoped 版本，见
   `relay/duty.py::_pwsh_path`。
3. **失联让位上游能力**：给 `command_handoff` 增加受证据约束的
   `no_heartbeat`/stall 让位信号；在此之前保持
   `EMERGENCY_REQUIRES_UPSTREAM`，不要伪造 `time_limit`。
4. **真实唤醒闭环**：需要目标原会话已经打开且 UI profile 的
   `session_marker` 可见。2026-11-22 的尝试因 qoder 当前窗口是另一个调研
   会话而被 `SESSION_MARKER_MISSING` 阻断；不要对同一窗口重复尝试，也不要
   改投另一个窗口来“碰运气”。正确做法是让目标回到原会话，或由人重新校准
   并记录新的 session fingerprint。
5. **本地模型验收**：启动 LM Studio 后再跑 `brief`，把真实输出与
   `UNAVAILABLE`/`ABSTAINED` 分支都留下证据；仍然只作为 advisory，不写角色状态。

### 14.3 不要做的事

- 不要为了“让流程走通”而把 `SESSION_MARKER_MISSING`、`SKIPPED_PWSH_MISSING`
  或 `DISPATCH_FAILED_OR_UNKNOWN` 改写成成功；
- 不要在接受换防时自动补心跳；新持有者必须自己 `beat`；
- 不要修改其它 claim 的 `cli.py`、`coalition/` 或 `coordination/` 文件来
  绕过阻塞；先在信箱里留证据并等归属者处理。