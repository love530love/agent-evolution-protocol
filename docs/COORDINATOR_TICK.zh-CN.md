# 居中协调一拍：把三条既有单步接起来

记录日期：2026-11-05（v0.6.21，对应 `COORDINATOR_VISION_EVIDENCE` 的 P0）

## 1. 要解决的不是「没有调度」，而是「三拍彼此不通」

在写这份文档之前，只读盘点确认项目里已经有**三条各自完整、但互不调用**的单步链：

| 既有单步 | 内部做什么 | 是否含真发 |
|---|---|---|
| `coordination/campaign_tick.py:tick()` | `presence.reconcile` → `team_dispatch.process` → `command_memory.ingest_journal`（协作区与 bridge 各一次）→ `campaign_cycle.reconcile`（ACK/工作截止、容量释放、超时接力、通知修复） | 否（写 mailbox / 状态） |
| `coordination/mission_pulse.py:pulse(apply=True)` | 扫 `missions/M-*.json`，过 11 类硬闸门 + 复算，把 `ready` 阶段投成 `DEPOSITED` 订单 | 否（`submit_order` 明写 `Nothing is sent until a tick runs`） |
| `federation/supervisor.py:step()` | `_settle` 结案 → `hold_alerts.emit` → `engine.tick` 排程 → `_claim_scopes` 租约 → **`commander.tick` 真发** | **是** |

第三条已经是一个完整的一拍，并且返回一个统一回执。所以 P0 **绝不能重写它**，
更不能造出第四条链。本版只新增一个薄接线层 `coordination/coordinator_tick.py`：
一次性、零模型、零子进程、零常驻，默认只读，`--apply` 才按固定顺序调用上述既有函数。
**不改任何既有函数签名，不新增 schema，不新增 `coord-*` 命令面，不改 mission 文件。**

## 2. 固定顺序与两条硬安全性质

```text
--apply  →  campaign_tick.tick()  →  mission_pulse.pulse(apply=True)  →  恰好一条投递路径
                                                                        ├─ federation/supervisor.step()  （有 DAG 上下文）
                                                                        └─ commander.tick()             （无 DAG 上下文）
```

**顺序的理由**：与既有 `commander.tick()` 的「先结案 → 排程 → 认领 → 最后真发」一致。
先结清回表与截止，状态才是已知的；再让 mission 入队；最后才允许把消息送出去。
任一步前置失败即 `halted=SETTLE_FAILED` / `MISSION_FAILED` 并**立即停止**，
不排队、不真发——状态不明时继续推进就是拿不确定状态去发消息。

**性质一：投递路径严格二选一。** `supervisor.step()` 内部已经调用 `commander.tick()`。
若两者都调，或多调一次，就是**重复投递**（项目记忆里已把「已可能发出再换」列为禁区）。
`dispatch_path` 在执行前就写定，回执里只有一个值。

**性质二：失败不回退。** `supervisor.step()` 抛错时，它可能已经发过消息了；
此时改调 `commander.tick()` 不是重试，是**第二次发送**。所以 federation 路径失败后
只记录 `stages[-1].status=failed`，`dispatch_path` 保持 `federation_step`，绝不 fallback。

DAG 上下文必须同时给 `--kernel-root`、`--workspace`、`--agents` 才启用；缺任一项时
回报 `skipped[].reason=FEDERATION_CONTEXT_MISSING`，**明确记为「没跑过」而不是「跑成功了」**。

## 3. 默认只读预览

不带 `--apply` 时只调用只读函数，零写入：

- `mission_pulse.pulse(apply=False)` 的 `scanned / candidates / verified / rejected`；
- `campaign_cycle.status()`、`presence.snapshot()`、`commander.status()` 的计数；
- `campaign_tick.next_due()` 汇总的最近截止时间（`next_due_source` 字段标明来源，不自算）；
- `plan`：将要调用的四行既有单步清单。

某个只读源损坏时写 `unavailable: <异常>`，不猜、不补、不让整个预览崩掉。
空仓也必须报出扫描量，避免「扫描器坏了」和「机器很干净」看起来一样。

## 4. 回执与自动边界

| 字段 | 含义 |
|---|---|
| `applied` / `writes_performed` | 是否真的写、写了几步 |
| `stages[]` | 每步的 `callable / status / result / error`；只有实际执行的步才进这里 |
| `skipped[]` | 明确没跑过的步及原因 |
| `dispatch_path` | `none` / `commander_tick` / `federation_step`，执行前写定 |
| `halted` | `""` / `SETTLE_FAILED` / `MISSION_FAILED` |
| `models_loaded=0`、`processes_started=0`、`ui_touched=false`、`deployment_performed=false`、`system_tasks_registered=false` | 硬边界，任一变真即越界 |

| 动作 | 是否自动 |
|---|---|
| 只读预览、汇总最近截止、给出调用计划 | ✅（默认） |
| `--apply` 时按固定顺序调用既有单步 | ✅（需显式开关） |
| 判断某个 Agent 是否接单、产出任务结论 | ❌ 由目标 Agent 与回执表负责 |
| 改 mission 阶段 / 接管他人 claim / 评审晋升 | ❌ |
| 启动模型、常驻进程、后台线程、OS 计划任务 | ❌ |
| 关单 | ❌ **只有 `VERIFIED` 能关单** |

`DEPOSITED / WINDOW_FOUND / INPUT_SUBMITTED / MESSAGE_VISIBLE` 不是送达；
`WAKE_QUEUED_UNVERIFIED / RELAY_QUEUED_UNVERIFIED / MAILBOX_ONLY / REJECTED` 不是接单。

## 5. 触发层仍然归操作员

`scripts/run-coordinator-tick.ps1` 是一次性有界脚本：纯 ASCII、不循环、不 sleep、
不后台运行、不注册任务，默认不带 `--apply`。是否周期触发、多久一拍，完全由操作员或
OS 计划任务决定。两条既有约束不变：

1. 由 agent 内部 spawn 的常驻进程会随工具调用结束被**作业对象回收**，
   `CREATE_BREAKAWAY_FROM_JOB` 不被允许；常驻只能由 agent 之外的东西建立。
2. 操作员明确要求不要让机器卡顿；本项目不引入常驻 watcher / 后台线程 / 定时任务。

**定时器只负责触发只读命令，定时器不是判断。**

## 6. 验收

- `tests/test_coordinator_tick.py`：空仓 dry-run 零写入、dry-run 不调任何写步骤、
  只读预览五项来源与 `next_due_source`、计划固定顺序、`--apply` 三步顺序、
  前置失败不真发、DAG 上下文缺失明确 `skipped`、两条投递路径互斥、
  **federation 失败绝不回退二次投递**、回执硬字段、
  AST 守线（无 socket/urllib/subprocess/threading/asyncio/模型 import、无 `while`、无 sleep）、
  CLI JSON 可解析且默认 dry-run、能力卡与本文档存在、触发脚本纯 ASCII 且默认不 `--apply`。
- 全量回归与 `tests/test_capability_cards.py` 一并跑，结果见 `docs/releases/v0.6.21.md`。
- 真协作区只读预览、临时沙盒 `--apply` 幂等另做人工复核。

## 7. 本次未做（不要在别处写成已完成）

- ~~没有实现 P1 per-Agent 额度账本~~ —— **v0.6.22 起该命题已移出项目范围**：
  「省积分」属使用侧成本，不是本项目目标，也不再是欠账；
  详见 `docs/OPERATIONAL_FIDELITY_AND_RELAY_DRILL.zh-CN.md`；
- 没有实现 **P2** claim↔evidence 自动核验、共同来源去重、`quorum` 接进派工路径；
- 没有把唤醒/插话/读表/回表做成 MCP tools 或 A2A Agent Card（P3）；
- 没有接线 `artifact_manifest.verify`、通知定级、跨重启恢复、持久化调度游标（P4）；
- 没有注册任何 OS 计划任务，没有启动常驻进程、后台线程或模型；
- 没有引入任何外部运行时依赖（Temporal Service / Postgres / LiteLLM Proxy 一律未部署）；
- **不承诺 7x24 完全无人值守，不宣称「代替人类工程师」。**
  「省积分」自 v0.6.22 起移出项目范围，既不需证明也不作为欠账。

接线只是让既有能力被同一次调用按顺序触发；它不补上愿景基线第 2 节里
「有代码无消费者」的全部缺口，也不改变 `docs/NOVICE_LONGRUN_WORKFLOW.zh-CN.md` 的结论。
