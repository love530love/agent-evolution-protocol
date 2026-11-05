---
name: aep-start-dispatch
description: 新 Agent 从零接入协作区、认领任务、发起调度派工，并按回执判定是否真的被接单；只写文件也能完成最低路径。
min_agent_capability: file-only
model_required: false
---

# 新 Agent 开始调度任务

## 何时用

满足任一条即执行：

- 一个新 Agent 刚接入，被要求「开始调度任务」「派工」「接手某个任务 ID」；
- 已有任务 ID（形如 `T162`），需要先查协作区再决定能不能动手；
- 需要给别的 Agent 发指令，并要知道「对方到底接没接单」。

不用于：只读文档、跑单条测试、修改自己工作区源码（这些不需要协作区）。

## 前置条件

1. 当前目录是仓库根，`coordination/` 与 `.aep-kernel/` 存在。`aep` 可执行；
   用 `aep --help` 确认命令面，用 `python -c "import agent_evolution_protocol as a; print(a.__version__)"`
   确认版本与 `pyproject.toml` 一致。
2. **先确认 Agent 名字，不要自造**。执行 `aep --coord-root coordination coord-status`，
   从 `sessions` 里找已登记的名字，或问人类。`coord-send --to` 只校验字符格式、
   **不校验成员**，写错会静默生成一个没人读的信箱。名字须匹配 `[A-Z0-9][A-Z0-9_-]{0,63}`
   （不区分大小写，命令内部会转成小写）。
3. 所有 `aep` 命令带 `--coord-root coordination`；涉及会话/lease 再加 `--kernel-root .aep-kernel`。
4. 动手前查 `docs/coordination/skill-manifest.json` 的风险分级：`read-only` 可以直接跑，
   `mutating` / `irreversible` 要先过闸门。命令名以该清单为准，不要凭记忆拼。
5. 派工契约字段见 `schemas/dispatch-envelope-v1.schema.json`；结论口径见
   `docs/DELIVERY_TRUTH_MATRIX.zh-CN.md`。

## 步骤

### 路径 A：只有文件能力（最低能力，也能发起一次派工）

1. 按 `docs/coordination/ANY_AGENT_CAN_DISPATCH.md` 的结构，写
   `coordination/delegation/inbox/<id>.json`：
   - `id` 格式 `DYYYYMMDDTHHMMSSffffffZ-12位小写十六进制`，**文件名与 id 完全相同**；
   - `from` / `to` 用第 2 步确认过的名字；`reply_due_at` 带时区且不超过 3 小时；
   - `allow_new_session: false`。
2. 抄 `coordination/delegation/receipts/<id>.json` 里的状态字段。
3. 查下面的「回执判定表」。**此路径不能宣称任务已被接单**，只能宣称「已排队」。

有命令行时，`scripts/ask-teammate.ps1` 是同一动作的封装（`-From -To -Task -Instruction -Evidence -SessionId`）。

### 路径 B：有命令行（完整调度）

1. 接入，**不新建会话**：

   ```
   aep --coord-root coordination --kernel-root .aep-kernel join --agent <name> --task <TASK> --workspace <path> --markdown
   ```

   抄输出里的会话策略（`RESUME_EXISTING` / `BLOCKED_HANDOFF_REQUIRED` / `NEW_SESSION_WITH_HANDOFF`）
   与 unread digest 数量。详见 `docs/JOINING_ANY_TASK.zh-CN.md`。

2. 首读上下文（只读优先）：

   ```
   aep --coord-root coordination coord-status
   aep --coord-root coordination coord-digest --agent <name>
   aep --coord-root coordination --kernel-root .aep-kernel inspect --markdown
   aep --kernel-root .aep-kernel kernel-continuation-plan --agent <name> --workspace <path>
   ```

   需要写接入记录时才跑 `coord-onboarding --agent <name>`（mutating）。
   纪律见 `docs/AGENT_ONBOARDING_CONTRACT.zh-CN.md`。

3. 写动作前认领 / 加锁：

   ```
   aep --coord-root coordination coord-claim --agent <name> --task <TASK> --paths <file> --ttl-hours 48
   aep --kernel-root .aep-kernel kernel-lease-acquire --lease-type <type> --resource <res> --executor <name>
   ```

   任务已被别人 claim → 读 owner 状态、digest 和 task-state，**不要抢写**。

4. 派工（先预览或 dry-run，再正式发）：

   ```
   aep --coord-root coordination coord-dispatch --order-id <id> --show-body
   aep --coord-root coordination coord-wake --from <name> --to <target> --task <TASK> --reason "<reason>" --dry-run
   aep --coord-root coordination coord-assign --workspace <path> --task <TASK> --target <target> --requirement "<text>"
   aep --coord-root coordination coord-send --from <name> --to <target> --topic <topic> --kind update --text "<text>"
   ```

   `coord-assign` 是一步写 DAG 节点 + 取 lease + 投递订单（irreversible）；
   `coord-send` 同为 irreversible。`coord-dispatch` 只预览排队订单，不碰桌面。

5. 查回执（只读）：

   ```
   aep --coord-root coordination coord-delivery-status --to <target> --id <event-id>
   aep --coord-root coordination coord-delivery-audit
   aep --coord-root coordination coord-ack --order-id <id>
   ```

6. 按「回执判定表」得结论。**只有 `VERIFIED` 能关单**；其余一律继续等或升级。

7. 更新状态：

   ```
   aep --coord-root coordination coord-task-state --agent <name> --task <TASK> --phase working --goal "<goal>"
   ```

   需要人类决策时加 `--human-required`（会让待决项被看见且禁止自动接管）。

8. 失败时先跑，不要自由重试：

   ```
   aep --coord-root coordination coord-guided-retry --attempted-action "<what>" --error "<error>" --risk-level medium
   ```

## 回执判定表

| 字段 | 取值 | 结论 | 下一步 |
|---|---|---|---|
| `join` 会话策略 | `BLOCKED_HANDOFF_REQUIRED` | 不能接续旧会话 | 停止，升级给人类；**不要新建会话绕过** |
| `join` 会话策略 | `NEW_SESSION_WITH_HANDOFF` | 需显式交接 | 停止，等人类或 wake 明确允许 |
| `coord-claim` | owner 已是别人 | 不能写 | 读 owner 状态；停止 |
| `receipts[].status` | `WAKE_QUEUED_UNVERIFIED` | 已排队唤醒，未接单 | 继续查回执，不得关单 |
| `receipts[].status` | `RELAY_QUEUED_UNVERIFIED` | 请队友中继，未接单 | 继续查回执，不得关单 |
| `receipts[].status` | `MAILBOX_ONLY` | 只落了信箱 | 继续查回执，不得关单 |
| `receipts[].status` | `REJECTED` | 验证失败 | 停止，升级给人类 |
| 投递回执 | `DEPOSITED` / `WINDOW_FOUND` / `INPUT_SUBMITTED` / `MESSAGE_VISIBLE` | 都不算送达 | 继续查 `coord-delivery-status` |
| 投递回执 | `AGENT_ACK` / `WORKING` / `EVIDENCE_RETURNED` | 对方已响应 | 继续等 `VERIFIED` |
| 投递回执 | `VERIFIED` | **唯一可以关单的状态** | 结束 |
| 目标状态 | `activity_state=busy` | `AGENT_BUSY/DEFERRED` | 保持原 `event_id`，等下一次只读 readiness probe；不得换通道重发 |
| `coord-guided-retry` | `uncertain-outcome` | 写结果未知 | 停止；不得重放写操作，必要时标 `UNKNOWN` |
| `coord-delivery-status` | 无该 `id` 记录 | 没有可证明的投递 | 停止，不要推断「对方应该收到了」 |

## 失败处置

- 一律三选一：**停止 / 延期 / 升级给人类**。禁止自由重试、换会话、绕过闸门。
- 禁止把普通消息当 wake、禁止消费旧 wake 队列、禁止为绕过上下文缺失而新建会话。
- 禁止对 `submit` / `upload` / `publish` / `delete` / `payment` 等高风险动作自动重试。
- 桌面忙态时不得覆盖输入框、不得新建会话、不得盲目换通道；判据见
  `docs/AGENT_OPERATION_GUIDE.zh-CN.md`。
- 回执只有「已收到」一句空 ACK 时，按
  `docs/coordination/EVIDENCE_BACKED_AGENT_HANDOFF.zh-CN.md` 要求补分析正文、摘要与 SHA-256。
- 不要承诺「一句话启动多天无人值守任务」：`docs/NOVICE_LONGRUN_WORKFLOW.zh-CN.md`
  明确写了 mission planner 与持久化 DAG runner 尚未完成。`scripts/start-mission.ps1`
  只返回 `mission_id`，不等于阶段已被自动执行完。

## 验收门

```
python -m pytest -q tests/test_capability_cards.py
```

期望全绿。该测试守：卡片六节顺序、`coord-*` 命令名真实存在于
`docs/coordination/skill-manifest.json`、引用的文档/测试路径真实存在、file-only 卡真的给出只写文件路径。

补充自检：只跑第 2 步的只读命令后，`git status --porcelain` 不应出现新的 `coordination/` 文件
（该目录被 `.gitignore` 覆盖）；若出现，说明误跑了 mutating 命令，停止并报告。

## 边界

本卡片只负责「接入 → 认领 → 派工 → 判定回执」。它不授予外部写权限，
不自动唤醒任何客户端，也不改协作区之外的源码。真正的关单证据是 `VERIFIED`，
不是文件存在、消息可见或一句「已收到」。
