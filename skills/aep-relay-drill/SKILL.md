---
name: aep-relay-drill
description: 在线 Agent 依次互派、回环总结并判定是否全部 VERIFIED；零模型、默认只读，绝不代报在线或伪造 ACK。
min_agent_capability: cli
model_required: false
---

# aep-relay-drill

目标是**操作拟真度**：让手脚流畅、快速、连贯地完成调度、分工派发、唤醒、回表与推进，
像人类工程师在现场操作。完成判据由操作员指定：**在线 Agent 依次互相派发并回环总结，
全部通过才算跑完**。

**「省积分」不是本项目目标**：积分上涨源于 Agent 执行阻滞/拖沓/顺畅度低，属使用侧成本；
本卡不追踪积分、额度、用量或重置间隔，也不拿实据去证明它。

## 何时用

- 要检验「多个在线 Agent 能否借本项目互相派工并回环总结」；
- 要判定一次交接到底是不是 `VERIFIED`，而不是靠「消息可见 / 已输入 / 已 ACK」猜；
- 新 Agent 加入后要确认它既当过发起方、也当过接收方。

**不适用于**：替代桌面投递、替 Agent 上报在线、伪造 ACK、计算成本积分、
自动改源码、关单之外的评审晋升、启动常驻 watcher。

## 前置条件

- 至少 **2 个** Agent 有**自己上报的、新鲜的**在线证据（`presence.effective(row)=="online"`，
  TTL 默认 30 分钟）。文件里写着 `online` 但过期不算；进程在跑也不算。
- 每个 Agent 能进入共享工作区、运行一条命令、写一份分析文件。
- 不需要模型、LM Studio、浏览器或桌面自动化；桌面投递由既有 `wake_daemon` /
  `coord-uia deliver` 负责，本卡不碰窗口。

各 Agent 自己执行（协调器**不代报在线**）：

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\scripts\join-agent.ps1' -Agent '<唯一_agent_id>' -SessionId '<原会话 ID>'
```

若跳过这一步，`team_dispatch.create()` 会以
`SENDER_NOT_ONLINE: <agent>; signal presence before commanding` 拒绝；
`acknowledge()` 同样校验接收方在线。这是协议故意设计的，不代报在线、
不绕过、不写死名单。

## 步骤

1. **只读预览**（默认，零写入）：

   ```text
   python -m agent_evolution_protocol.coordination.relay_drill --coord-root coordination
   ```

   抄回执：`fresh_online`、`blocked`、`legs`、`run_complete`、`how_to_signal`。
   `legs` 应成有向环：每个 Agent 各派一次、各收一次。

2. **按回执判定表判断**：`blocked` 非空就停，把 `how_to_signal` 转发给各 Agent；
   不要靠自己补在线证据绕过。

3. **确认后才派发**（唯一写入口）：

   ```text
   python -m agent_evolution_protocol.coordination.relay_drill --coord-root coordination --apply
   ```

   每条腿调用既有 `team_dispatch.create()`，并保存 plan 供重查。
   派发不是接单，更不是完成。

4. **每个接收 Agent 回环**：写一份真实分析，用同一 `event_id` 执行
   `handoff_worker --action accept`（会自报在线、写 SHA-256 归档、发 `AGENT_ACK`
   和发起方 `<event_id>-reply.json`）。

5. **重查判定**：再跑一次 `relay_drill`（会读已保存 plan），
   或单独跑 `loop_verdict`：

   ```text
   python -m agent_evolution_protocol.coordination.loop_verdict --coord-root coordination --bridge-root <bridge> --event-id <id> --sender <a> --target <b>
   ```

6. **收工**：只有 `run_complete=true` 且每条腿都是 `VERIFIED` 才算跑完。
   任何一腿未通过就继续，不得宣称完成，也不得用其它状态关单。

## 回执判定表

| 回执字段 | 结论 |
|---|---|
| `blocked=NEED_AT_LEAST_TWO_ONLINE_AGENTS` | 新鲜在线证据不足，**没有派工**；按 `how_to_signal` 让各 Agent 自己上线 |
| `fresh_online=[]` 但进程在跑 | 「OS 有窗口」≠「协议在线」，不能开工 |
| `SENDER_NOT_ONLINE` | 发起方在线证据过期/缺失，协议拒绝；不代报在线、不绕过 |
| `legs` 成环 | 各 Agent 依次各派一次、各收一次；只是计划，不是通过 |
| `applied=false` / `writes_performed=0` | 只读预览，零写入 |
| `dispatch_state=CREATED` | 只表示派工已建；**不是送达、不是接单** |
| `verdicts[].verdict=VERIFIED` | 三方证据（receipt `AGENT_ACK` + 重算 SHA-256 归档 + 发起方回表同哈希）一致 |
| `NOT_VERIFIED` + `reasons[]` | 具名缺口：`RECEIPT_NOT_ACK` / `ARTIFACT_DIGEST_MISMATCH` / `REPLY_HASH_MISMATCH` 等 |
| `run_complete=true` | **唯一**允许宣称「依次互测跑完」的结论 |
| `DEPOSITED` / `WINDOW_FOUND` / `INPUT_SUBMITTED` / `MESSAGE_VISIBLE` / `AGENT_ACK` | 都不是关单证据；**只有 `VERIFIED` 能关单** |

## 失败处置

- **绝不代报在线**：协调器替 Agent 写 `online` 就是自导自演，整个依次互测判据失效。
- **绝不伪造 ACK 或 VERIFIED**：回执只能来自接收 Agent 的真实分析与哈希。
- **绝不把「省积分」写进验收**：它不是本项目目标，也没有可复算数据。
- `session_marker` 闸门、`task_delivered` 见证人、UIPI 提权等桌面约束仍按
  `docs/DESKTOP_SEND_AND_URGENT_INTERJECT.zh-CN.md` 与
  `docs/HANDS_AND_FEET_STATUS_20261031.zh-CN.md` 执行；本卡不碰窗口。
- 桌面观察无法判定会话身份时**不投递**，避免把消息送进错误聊天。
- 失败只允许三选一：**停止 / 延期 / 升级给人类**。空 ACK 时按
  `docs/coordination/EVIDENCE_BACKED_AGENT_HANDOFF.zh-CN.md` 补分析正文、摘要与 SHA-256。

## 验收门

```text
python -m pytest -q tests/test_relay_loop.py tests/test_capability_cards.py
```

全量回归本机须带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`。完整口径、四层在线区分、
本轮实测阻塞与真机 SOP 见 `docs/OPERATIONAL_FIDELITY_AND_RELAY_DRILL.zh-CN.md`。
