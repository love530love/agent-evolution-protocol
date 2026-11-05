# 长任务心跳：显存疑问、既有骨架与 mission 接合器

记录日期：2026-11-05（v0.6.19）

## 1. 操作员提出的两个问题

1. 「LM 已启动，但是不是还要占用本地显存，不是很理想的方案」；
2. 「无人值守的长任务，是不是应该在项目里用小模型或者是用什么程序做一个定时的心跳机制，
   用来轮询或者是向当前指挥的 Agent 发送信息来确保长任务一直持续进行」。

这两个问题其实指向同一个混淆：把「保持任务活着」和「判断下一步做什么」当成了同一件事。

## 2. 显存：实测没有占用，且模型不在心跳路径上

当日在本机只读实测：

| 检查 | 结果 |
|---|---|
| `lms status` | `Server: ON (port: 1234)` |
| `lms ps` | **No Models Loaded** |
| `nvidia-smi` | RTX 3090，显存 **692 MiB / 24576 MiB**，GPU 利用率 **0%** |
| `GET /v1/models` | 只列出模型清单，未加载权重 |

「LM Studio 服务启动」与「加载模型占显存」是两件事。服务开着但没有 `lms load` 时，
权重不在显存里。项目里唯一会加载模型的路径是 `insight_miner --propose`，且强制
`--gpu off`（CPU 推理）、`--ttl 60`（60 秒后自动卸载）、按精确 identifier `unload`；
默认的 `insight_miner` 是 socket-free 纯规则路径。

**结论：可以直接关掉 LM Studio server，项目默认能力完全不受影响。** 模型缺席是设计好的
降级路径，不是故障。本次新增的接合器与模型零耦合，代码里没有 `lms`、没有模型 import、
没有显存占用。

## 3. 心跳不需要小模型

心跳要回答的是确定性、可复算的问题：租约到期没有、`last_progress_at` 有没有超过 900 秒、
phase 是不是终态、订单有没有 ACK。规则引擎零误差且能复算；概率模型没有「不知道」输出，
replace 不了 fail-closed 层。项目此前已就此立过约束：

- `docs/AUTONOMOUS_SUPERVISION_LOOP.zh-CN.md`：「不对静默状态进行无意义模型轮询」；
- `docs/MAILBOX_PRIORITY_AND_INTELLIGENCE_LOOP.zh-CN.md`：「禁止静默模型轮询」；
- `docs/releases/v0.6.13.md` / `v0.6.15.md`：**「定时器不是判断」**；
- `routing.py` / `diagnosis.py` / `recovery_policy.py` 的既定结论：安全层不能换概率模型。

让小模型常驻做心跳，恰好会制造操作员明确反对的三件事：常驻资源占用、静默空转、
弱 Agent 因模型能力差异跑不出一致结论。

## 4. 项目里本来就有心跳，而且是零模型的

| 层 | 既有实现 | 性质 |
|---|---|---|
| 判断 · 订单 | `coordination/commander.py:tick()` | 确定性单步：读盘、判 ACK/TTL/自动恢复、派发；不 sleep、不 spawn、不调模型 |
| 判断 · DAG | `federation/engine.py:tick()` | `_stalled()` 用 `last_progress_at` 比 `heartbeat_timeout=900`，无心跳 → `diagnosis.classify("no_heartbeat")` |
| 循环包装 | `commander.watch()`、`federation/supervisor.py:serve()` | 默认 30 秒 `while + sleep`，有界 `max_ticks` |
| 事件驱动 | `scripts/campaign-watch.ps1` + `campaign_tick next-due` | `FileSystemWatcher` + 按最近截止时间唤醒，不是固定间隔空转 |
| OS 触发 | `scripts/aep-supervisor.ps1`、`scripts/register-supervisor-task.ps1` | 登录时计划任务 + Mutex 单实例 |
| 入队 / 发消息 | `commander.submit_order()` → `tick` 真发 | 「只入队、不碰 UI」，订单 `state=DEPOSITED` |
| 续租 | `coord-claim-renew` | `federation/assignment.py:94-96` 明文写了「每 15 分钟续租，否则判失联」 |

所以正确的动作不是新建心跳，而是把断掉的线接回去。**本次新增的不是第二套调度器。**

## 5. 真正的断口

`scripts/start-mission.ps1` 只调 `mission_intake.create()` 写出 `missions/M-*.json`
（`state: planned` + 3 个 stage），然后退出。事实是：

- `cli.py` **没有任何 `mission` 子命令**，没有 runner / serve / watch 消费 `mission_id`；
- `mission_supervisor.inspect()` 只返回 `assign / observe / recovery_review /
  report_delivery / blocked_review` 这些**动作建议**，不执行、不循环；
- `mission_runtime.py` / `mission_planner.py` 用 `p.write_text()` 原地覆盖，非原子、无事件日志；
- `mission_graph.py` 是另一套未被消费的 DAG schema；
- `commander.tick()` 与 `engine.tick()` 都**没有持久化调度游标**（全量重扫 inbox，永不归档）；
- `docs/NOVICE_LONGRUN_WORKFLOW.zh-CN.md:45-47` 早已写明还缺 mission planner、
  持久化 DAG runner 和通知策略。

## 6. 接合器做了什么

流水线（一次外部调用 = 一拍，进程随即退出）：

```text
scan()  →  gates()  →  verify()  →  [仅 --apply] commander.submit_order()  →  DEPOSITED
```

- `scan()` 是只读的；动作建议取自 `mission_supervisor.inspect()`，不复制一套判定逻辑。
- 只有 `assign`（阶段 `ready`）允许入队；`observe` / `recovery_review` /
  `blocked_review` / `report_delivery` 一律只报告，交给人类。
- `gates()` 全部为硬拒绝：`ACTION_NOT_ASSIGNABLE`、`NO_TARGET`、
  `INVALID_AGENT_NAME`、`UNKNOWN_AGENT`、`MISSION_PAST_DEADLINE`、`HUMAN_REQUIRED`、
  `WAITING_FOR_EVENT`、`TASK_TERMINAL`、`CLAIM_OWNER_MISMATCH`、`CLAIM_EXPIRED`、
  `ALREADY_QUEUED`。
- **成员校验从文件推导**：`presence/agents/*.json` 与 `clients/*.json` 的实际文件名，
  不用会漂移的硬编码名单。`valid_agent()` 只校验字符，不校验成员。
- `verify()` 从文件重算 mission sha256、`event_id`、全部闸门与正文；任一漂移即拒绝。
  **拒绝的是旧证据，不是永久封禁这个文件**：下一次外部调用会重新 `scan()`，
  基于新内容产生新的 `event_id` 并重新过闸门；但绝不允许拿已被拒绝的 candidate 去写。
  `queue_candidate()` 是唯一的写入口，先 `verify()` 再 `submit_order()`。
- `event_id` 由 `(mission_id, stage_id, stage.state, stage.updated_at, goal)` 稳定哈希得到，
  重复运行同一状态不会生成第二单（幂等）。
- 唯一的写动作是 `commander.submit_order()`，`docstring` 明写
  `Nothing is sent until a tick runs`；不碰桌面、不发消息、不启动进程。

不新增 schema（`schemas/` 仍是原来的 7 个）、不新增 `coord-*` 命令面
（`docs/coordination/skill-manifest.json` 仍由 `build_parser()` 生成）、不改 mission 文件。

## 7. 触发层归 OS，不归仓库

- `scripts/run-mission-pulse.ps1`：一次性、有界、只读优先的调用脚本；默认不带 `--apply`。
- `scripts/register-mission-pulse-task.ps1`：**默认只打印计划，`-Register` 才会改系统**；
  带 `MultipleInstances=IgnoreNew` 与 5 分钟 `ExecutionTimeLimit`。
- **本版不注册系统任务**，没有执行过任何 `Register-ScheduledTask`。是否启用、多久一拍，
  完全由操作员决定。

这是两条硬约束的必然结果：

1. 本机由 agent 内部 spawn 的常驻进程会随工具调用结束被**作业对象回收**，
   `CREATE_BREAKAWAY_FROM_JOB` 不被允许，任何创建标志都救不回来；
   常驻只能由 agent 之外的东西建立（双击或 OS 计划任务）。
2. 项目刻意不引入常驻 watcher / 后台线程 / 定时任务（操作员要求不要让机器卡顿）。

定时器只负责触发只读命令，**不承担判断**。

## 8. 自动边界

| 动作 | 是否自动 |
|---|---|
| 读 mission、派生成员名单、重算闸门、生成 `event_id` | ✅ |
| 只读 dry-run 预览、报告 `scanned` 与全部拒绝原因 | ✅（默认） |
| `--apply` 时把通过复算的阶段**入队**为 `DEPOSITED` | ✅（需显式开关） |
| 真正把消息送进客户端 | ❌ 由既有 `coord-commander tick` 负责 |
| 改写 mission 阶段 / 自动接管他人 claim | ❌ `automatic_takeover_allowed` 恒 `False` |
| 启动模型、常驻进程、后台线程、OS 计划任务 | ❌ |
| 自动评审、晋升、改源码、commit / push / release | ❌ |
| 关单 | ❌ 只有 `VERIFIED` 能关单 |

回执里的硬字段：`models_loaded=0`、`processes_started=0`、`ui_touched=false`、
`deployment_performed=false`、`system_tasks_registered=false`。任何一个变成真值都算越界。

## 9. 验收

- `tests/test_mission_pulse.py`：空仓扫描量、不可读文件、四种非 assign 动作、
  11 类闸门拒绝、dry-run 零写入、`--apply` 只产生 `DEPOSITED` 且幂等、
  workspace_id 透传、复算防漂移、伪造 `event_id` 拒绝、
  AST 守线（不 import socket / urllib / subprocess / 模型 / 后台线程）、
  脚本纯 ASCII 且默认**不注册系统任务**、能力卡与本文档存在。
- 全量回归与 `tests/test_capability_cards.py` 一并跑，结果见 `docs/releases/v0.6.19.md`。

## 10. 本次未做（不要在别处写成已完成）

- 没有 LM Studio / Ollama 端到端真实推理实测（此前 `lms ls` 挂起被终止）。
- 没有注册任何 OS 计划任务，没有启动任何常驻进程。
- 没有实现持久化调度游标、跨重启自恢复演练、统一 artifact 验收器、多日预算与配额调度。
- 没有实现通知策略；「只在需要人处理时通知」仍未落地。
- **不承诺 7x24 完全无人值守**：OS 计划任务本身还需要登录会话，且
  `docs/NOVICE_LONGRUN_WORKFLOW.zh-CN.md` 的 mission planner / DAG runner 缺口仍在。
