# 操作拟真度与在线 Agent 依次互测

记录日期：2026-11-05（v0.6.22）

## 1. 口径修正：省积分不是本项目的目标

操作员 2026-11-05 明确纠正：**「省积分」只是背景说明，不需要项目拿实据去证明，
也不该作为验收目标**。积分/用量上涨的根因是 Agent 在执行步骤上阻滞多、拖沓、
顺畅度低，属于**使用侧成本**，不是协议侧职责。项目不追踪积分、额度、token 用量、
限流、重置间隔或费用。

因此 `docs/COORDINATOR_VISION_EVIDENCE.zh-CN.md` 中原先把「省积分算不出来」列为
一号证伪、把 P1「per-Agent 额度账本」列为下一期的写法**已作废**：不是被证伪，
而是**整条命题移出项目范围**。上一版 `docs/COORDINATOR_TICK.zh-CN.md` 第 7 节里
「P1 额度账本仍未做」也不再是欠账。

需要区分清楚：「观点需要实据」这条仍然成立，且对回环判定、来源核验、错误情报
依然是硬要求；只是不该套在「省积分」这个不属于本项目的命题上。

## 2. 项目真正要解决的问题

> **操作拟真度**：手脚能否流畅、快速、稳定地达成所需操作 —— 调度、分工派发、
> 唤醒、插话、轮询、通报全队、催促查看回表、促成辩论与推进，达到「像人类工程师
> 在现场操作」那样的连贯程度，而不是每一步都要人补位、重试或猜。

注意这是**操作质量目标**，不是「项目已代替人类工程师」的身份断言。后者此前已被
v0.6.20 的事实基线否掉（无 OS 任务注册、无常驻进程、无跨重启恢复、VERIFIED 关单、
reviewer≠author），本文件不推翻它。

## 3. 新的完成判据（操作员指定）

> 后续应着重**在在线的 Agent 当中测试**：让他们利用本项目对其他 Agent 分工派发消息
> 并回环总结，**各个 Agent 依次地互相测试通过之后，才能算是跑完**。

拆成可复算的四条：

1. 参与的每个 Agent 都有**自己上报的、新鲜的**在线证据；
2. 每个 Agent **各发起一次、各接收一次**（有向环，不允许只由协调器单向派工）；
3. 每一环都有同一 `event_id` 的**分析正文 + SHA-256 + 发起方回表**；
4. **全部环节 `VERIFIED`** 才允许 `run_complete=true`；派发、可见、已输入、已 ACK
   都不算跑完。

## 4. 四层「在线」必须分开说

| 层 | 含义 | 本轮实测 |
|---|---|---|
| ① 文件写着 online | `presence/agents/*.json` 的 `state` 字段 | 4 个：codebuddy / codebuddy_cn / qoder_cn / codex |
| ② 有新鲜在线证据 | `presence.effective(row)` 真的返回 online | **0 个**（过期 4~7 天） |
| ③ 进程真的在跑 | OS 层主窗口进程 | 6 个：ChatGPT 34244、CodeBuddy CN 43924、Qoder 75180、Qoder CN 118632、WorkBuddy 93060、WorkBuddyAI 54400 |
| ④ 窗口真的可操作 | 通过 `session_marker` 闸门 + UIA/OCR 探针 + 发送后回读见证 | 当日仅 qoder / qoder_cn 有投递与插话证据；其余未复测 |

只读实测（2026-11-05 16:11 GMT+8）：

```text
4 个 presence: file_state=online 但 effective=stale
team_dispatch.create(sender=workbuddyai, to=qoder_cn, ...)
    → BLOCKED_BY_PROTOCOL: SENDER_NOT_ONLINE: workbuddyai
delegation/inbox 文件数 27 → 27（零写入）
执行器: High Mandatory Level, IsInRole(Administrator)=True  → UIPI 不是阻塞
```

**结论：物理上能投递，协议上不能开工。** 而且这是正确设计 ——
`team_dispatch.create()` 与 `acknowledge()` 都会调 `_presence()` 校验
`effective(row)=="online"` 与 session_id。在线证据必须由**每个 Agent 自己上报**
（`scripts/join-agent.ps1`）；协调器若替它们写 online，就变成自导自演，
「依次互测」这个判据从根上失效。

## 5. 补齐的两个地基

### 5.1 `coordination/loop_verdict.py` —— 第一次能判定 VERIFIED

此前全仓唯一产出 `RESULT_VERIFIED` 字面量的是 `dual_channel.WakeState`，且 src 内
**没有任何调用方**（只在测试里出现，还要求调用方自己传 `result_verified=True`）。
也就是说「只有 VERIFIED 能关单」是文档口径，代码回答不了「这次回环是否通过」。

新模块按 `docs/coordination/EVIDENCE_BACKED_AGENT_HANDOFF.zh-CN.md` 重算三方证据：

1. `delegation/receipts/<event_id>.json` 为 `AGENT_ACK` 且 `agent_accepted=true`、目标匹配；
2. `delegation/artifacts/<event_id>/<target>.json` 的 SHA-256 **从存储的分析正文重算**；
3. 发起方 `<event_id>-reply.json` 与回执 `reply` 都含**同一哈希**。

三者一致才 `VERIFIED`；否则 `NOT_VERIFIED` 并给出具名原因
（`REQUEST_MISSING` / `RECEIPT_NOT_ACK` / `ARTIFACT_DIGEST_MISMATCH` /
`RECEIPT_HASH_MISMATCH` / `REPLY_MISSING` / `REPLY_HASH_MISMATCH` /
`TARGET_MISMATCH` / `SENDER_MISMATCH`）。纯只读、零模型、零进程。

### 5.2 `coordination/relay_drill.py` —— 依次互派编排

- `fresh_online()` 只认 `presence.effective(row)=="online"`，不看 `state` 字段；
- 生成**有向环** `agents[i] → agents[(i+1) % n]`，每个 Agent 恰好派一次、收一次；
- 默认 dry-run 只出计划 + 阻塞原因（`NEED_AT_LEAST_TWO_ONLINE_AGENTS`）+
  每个 Agent 的自报在线指令，**零写入、不代报在线、不伪造 ACK**；
- `--apply` 才对每条腿调用既有 `team_dispatch.create()`，并保存 plan 以便重查；
- `status` 对每条腿跑 `loop_verdict.check()`，`run_complete` 只有**全部 VERIFIED** 才为真。

## 6. 真机演练 SOP（尚未跑完）

1. **各 Agent 自报在线**（每个 Agent 自己在共享工作区执行，TTL 30 分钟）：
   ```powershell
   & 'K:\PythonProjects5\agent-evolution-protocol\scripts\join-agent.ps1' -Agent '<唯一_agent_id>' -SessionId '<原会话 ID>'
   ```
   协调器不得代跑这一步。
2. **协调器只读预览**：
   ```text
   python -m agent_evolution_protocol.coordination.relay_drill --coord-root coordination
   ```
   确认 `fresh_online ≥ 2`、`legs` 成环、`blocked=""`。
3. **按腿派发**：加 `--apply`，每条腿生成一个既有 `team_dispatch` 请求。
   桌面投递仍由既有 `wake_daemon` / `coord-uia deliver` 负责，本模块不碰窗口。
4. **每个接收 Agent 写真实分析**并用同一 `event_id` 执行
   `handoff_worker --action accept`（内部会自报在线、写 SHA-256 归档、发 `AGENT_ACK`
   与发起方回表）。
5. **回环总结**：重跑 `relay_drill`（会读已保存 plan），抄 `verdicts` 与
   `verified_legs`。
6. **收工条件**：`run_complete=true` 且每一腿 `VERIFIED`。任何一腿未通过就继续，
   不得宣称跑完。

## 7. 明确未完成（防止被写成跑完）

- **真机「各个 Agent 依次互相测试」本轮没有跑完**：新鲜在线证据为 0，
  实测派工被 `SENDER_NOT_ONLINE` 拒绝，没有发起任何真实互派；
- 没有操作任何桌面窗口、没有发送任何消息、没有替任何 Agent 上报在线或 ACK；
- 没有验证 ④ 层「窗口可操作」的 session_marker 闸门（当日只读 `coord-uia observe`
  未返回可判定的输出，因此不做投递，避免投进错误会话）；
- 没有实现桌面投递的自动重试/插话编排（仍是既有 `coord-uia` / `wake_daemon` 职责）；
- 不追踪积分/额度/成本，不部署外部运行时，不承诺 7x24 无人值守，
  不宣称「代替人类工程师」。

## 8. 验收

- `tests/test_relay_loop.py`：完整三方闭环 → VERIFIED；缺任一环各自拒绝；
  重算 sha256 防篡改；`check` 只读；0/1 个在线 Agent 明确阻塞；stale 不计在线；
  3 个在线成环且各派各收一次；dry-run 零写入且**不断言 presence.signal 被调用**；
  `--apply` 每腿一条派工且不代报在线；只有全部 VERIFIED 才 `run_complete`；
  AST 守线（无网络/进程/模型 import、无 `while`）；能力卡与本文档存在。
- 全量回归与真协作区只读预览结果见 `docs/releases/v0.6.22.md`。
