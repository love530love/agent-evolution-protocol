# 无人值守本地任务队列（coord-unattended）

## 结论

`coord-unattended` 解决的是“任务已经排好，但所有 Agent 都不在线”这一段时间：
它把本地命令写成不可变、带哈希的任务清单，每个 tick 最多执行一个就绪任务，
并把退出码、输出文件大小和 SHA256 固化成回执。

它**不**替代 `federate serve`，也**不**把进程退出冒充 Agent ACK。在线 Agent
仍然优先走现有的 `assignment.assign` 派工；只有在线名单为空且调用方显式提供
本地计划时，任务才进入本地队列。

## 1. 两种路由

### 有在线 Agent：照常派工

```powershell
aep --coord-root coordination coord-unattended dispatch `
  --federation-root .agent-evolution `
  --workspace-id WS-EP02 `
  --task EP02-KF-S007 `
  --requirement-file tasks/s007-requirement.md `
  --agents workbuddy qoder `
  --local-manifest-file tasks/s007-local.json
```

- `presence.snapshot()` 中状态为 `online` 且租约未过期的 Agent 才算候选。
- 选中后调用 `assignment.assign`：写 DAG 节点、取写租约、投递订单。
- 在线只是自报状态，不等于接单，也不等于 ACK；投递和验收仍按原有证据链。
- 候选顺序按 `--agents` 给出的顺序，不按窗口是否可见猜测能力。

### 全部离线：执行显式本地计划

```powershell
aep --coord-root coordination coord-unattended dispatch `
  --workspace-id WS-EP02 --task EP02-KF-S007 `
  --requirement-file tasks/s007-requirement.md `
  --agents workbuddy qoder `
  --local-manifest-file tasks/s007-local.json
```

没有在线候选时，`--local-manifest-file` 会被写入队列，状态为 `QUEUED`；
它只是“已排好”，这一刻还没有进程运行。

### 全部离线且没有本地计划：保持等待

返回 `route=waiting`，不做任何猜测。可以补本地计划，或者发 `coord-wake` 后让
supervisor 继续换人。

## 2. 任务清单

```json
{
  "task_id": "EP02-KF-S007",
  "workspace": "D:/ShortDramas/order37",
  "goal": "渲染 ep02 s007 关键帧",
  "argv": [
    "H:/PythonProjects3/Win_ComfyUI/.venv/Scripts/python.exe",
    "tools/render_keyframe.py",
    "--shot", "s007",
    "--out", "03_keyframes/ep02/s007.png"
  ],
  "expected_outputs": ["03_keyframes/ep02/s007.png"],
  "timeout_seconds": 1800,
  "priority": 10,
  "depends_on": [],
  "max_output_bytes": 524288,
  "created_by": "workbuddyai"
}
```

规则：

- `task_id` 只能匹配 `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`；重复 ID 只有在清单
  哈希完全相同时才幂等返回 `ALREADY_QUEUED`，否则拒绝。
- `workspace` 必须是已存在目录；执行时它就是 cwd。
- `argv` 是字符串数组，**不经过 shell**；管道、重定向、`&&` 不会生效。
- `expected_outputs` 只能是 workspace 内的相对文件路径；`..`、绝对路径和
  盘符相对路径会被拒绝。目录不作为输出证据；需要目录时写一个完成标记文件。
- `depends_on` 指向其他任务；入队时检查环。依赖未成功前不会执行。
- `timeout_seconds` 上限 24 小时；超时后进程树会被终止，但结果记为 `UNKNOWN`，
  不自动重跑。
- `stdin` 可选，作为子进程标准输入。
- 清单是**可信本地输入**：能写这个文件的人本来就能直接运行这条命令。不要把
  Token、密码、Cookie 放进 `argv` 或 `stdin`；回执只会保留 stdout/stderr 的
  最后 4000 字符，但它仍然写在本地磁盘上。

## 3. 状态与证据

| 状态 | 含义 | 是否自动重试 |
|---|---|---|
| `QUEUED` | 已入队，没有进程运行 | 就绪时自动执行 |
| `RUNNING` | 当前 tick 正在执行 | 不重跑；崩溃后需人工核对 |
| `SUCCEEDED` | 退出码 0 且所有声明输出存在，已记录大小/SHA256 | 终态 |
| `FAILED` | 非零退出、未启动、或缺少声明输出 | 终态；需显式 `requeue` |
| `UNKNOWN` | 超时或 runner 无法给出可靠结果 | 终态；需 `--allow-unknown` 才可 requeue |
| `BLOCKED` | 依赖已进入 FAILED/UNKNOWN/CANCELLED | 依赖被 requeue 并成功后自动重新评估 |
| `CANCELLED` | 进程启动前被显式取消 | 终态 |

重要边界：

- `SUCCEEDED` 只证明“这个本地进程成功退出，并且这些文件存在且哈希已记录”。
  它不是 Agent ACK，也不是人工验收。
- 非零退出可能已经写了半个文件，所以默认不重跑；`requeue` 要求 `--actor` 和
  `--reason`，并生成新的 event id（`UNATTENDED-<task>-R2`），旧尝试进入 `history`。
- `UNKNOWN` 的第一次命令可能已经产生副作用；没有人工检查原始状态，不允许
  `requeue`。
- 队列不判断业务质量、不发布、不上传、不打开桌面窗口。

## 4. 命令

```powershell
# 入队
aep --coord-root coordination coord-unattended enqueue --manifest-file tasks/s007-local.json

# 只跑一个指定任务（依赖未就绪会返回 WAITING，不启动）
aep --coord-root coordination coord-unattended run --task EP02-KF-S007

# 按优先级自动取一个就绪任务
aep --coord-root coordination coord-unattended run

# 查看队列
aep --coord-root coordination coord-unattended status

# 持续消费：每 30 秒一个 tick，队列空闲即退出
aep --coord-root coordination coord-unattended serve --interval 30 --max-ticks 0

# 需要可控停止时给一个停止文件；文件出现后当前 tick 结束即退出
aep --coord-root coordination coord-unattended serve --stop-file coordination/unattended.stop

# 失败任务显式重排
aep --coord-root coordination coord-unattended requeue `
  --task EP02-KF-S007 --actor operator --reason "确认没有落盘，可安全重试"

# 取消尚未启动的任务
aep --coord-root coordination coord-unattended cancel `
  --task EP02-KF-S007 --actor operator --reason "改走别的镜头版本"
```

运行特性：

- 同一时刻只允许一个 runner；第二个进程得到 `RUNNER_BUSY`，不会抢任务。
- `serve` 每个 tick 最多一个任务；没有就绪任务时返回 `IDLE` 并退出，避免空转。
- 队列、回执和 journal 都在 `<coord-root>/unattended/`：
  `tasks/`、`receipts/`、`runs/`、`journal.jsonl`。
- 驻留主机可以直接消费队列：`aep-supervisor.ps1 -LocalQueue`（等价于
  `python -m agent_evolution_protocol.coordination.supervisor --local-queue`）。
  同一个循环里，只有“本 tick 没有把任何订单交给 Agent”时才执行至多一个本地
  任务；observe-only 模式永不执行。心跳记录的 `local` 字段只是本地进程证据，
  仍不是订单投递或 Agent ACK。
- 已经注册的 `AEP-DesktopHost` 计划任务要带 `-LocalQueue` 重新注册才会启用；
  本命令不自行注册计划任务，也不改系统权限。

## 5. 与现有执行面的关系

| 现有能力 | 负责什么 | 本队列不做什么 |
|---|---|---|
| `external_runner` | 一个有界命令、输出捕获、会话租约 | 不替代它；直接调用它执行 |
| `federate serve` / supervisor | 把工作区任务派给 Agent、回读、换人 | 不接管 Agent 任务状态 |
| `coordination/supervisor` watch（驻留主机） | 订单投递与回读；`--local-queue` 时顺带消费本队列 | 不把本地任务算作订单投递 |
| `nightly_loop` | 一个明确绑定的原会话上下行 | 不打开或读取任何聊天窗口 |
| `wake` / `commander` | 让离线 Agent 的会话被唤醒 | 不把本地进程算作 Agent 接单 |

因此，完整链路是：

```text
有在线 Agent -> assignment -> 订单 -> 同一 event_id 的 ACK/结果
全部离线     -> 显式本地计划 -> coord-unattended -> 进程/文件证据
两者都没有   -> waiting；不猜命令、不猜窗口、不盲重发
```

## 6. 验收

```powershell
python -m pytest tests/test_unattended_queue.py -q
```

当前 20 项通过，覆盖：清单校验、重复 ID、依赖环、成功输出的哈希、
缺失输出、非零退出、超时 UNKNOWN、失败不重跑、依赖阻塞与恢复、显式 requeue
的新 event id、未知状态需显式同意、取消、runner 互斥、serve 排空与 stop 文件、
在线派工（订单落 coord mailbox、DAG 落 federation root）、离线本地回退、
无计划等待。

另外做了一次 CLI 端到端冒烟：`enqueue -> run -> status` 返回
`QUEUED -> SUCCEEDED`，输出 `out.txt` 的 SHA256 已写入回执。

## 7. 已知边界

- 不做 GPU/CPU/显存资源调度；并行度固定为 1。需要并行时用多个独立队列根。
- 不做任务内 checkpoint；长任务应自己写阶段性输出，再用 `depends_on` 串起来。
- 不跨机器；只执行本机命令。
- 不提供自动回滚；`requeue` 只是重新执行，不会撤销第一次的副作用。
- `RUNNING` 在机器崩溃后会保持原状，必须人工检查原始命令的副作用后才能
  `requeue --allow-unknown`。

## 8. 掉线接手

2026-11-27 用户明确：协作区里主控（kernel/orchestrator，当前由本会话担任）
拥有最高接手权，因为队友经常因用量耗尽等原因掉线。执行含义：

- Agent 被判定为掉线或额度耗尽后，不再“等它上线”：按
  `docs/DISPATCH_STANDARD.zh-CN.md` 的重试与接管规则，改派仍在线且被授权的
  Agent，或把该任务的显式本地计划交给 `coord-unattended` / 驻留主机执行。
- 旧 Agent 归档为只读；恢复上线后先读交接包再继续，不重复投递同一 event id。
- 接手只改变“谁来做”，不降低证据标准：本地进程退出、文件哈希、订单投递和
  Agent ACK 仍是四种不同证据，不能互相冒充。
