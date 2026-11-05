---
name: aep-mission-pulse
description: 把已持久化的长任务 mission 阶段接回既有 commander 队列；零模型、一次性、默认只读预览，按回执判定是否允许入队。
min_agent_capability: cli
model_required: false
---

# 长任务心跳接合：mission → 既有队列

## 何时用

满足任一条即执行：

- `coordination/missions/M-*.json` 存在，某个阶段是 `ready`，需要推进长任务；
- 外部调度器触发了一拍心跳，需要判断「这一拍该不该给指挥 Agent 发消息」；
- 上一次没有派工，需要知道**为什么**没派工（而不是盲目重发）。

不用于：新建 mission（用 `scripts/start-mission.ps1`）、真正把消息送进客户端
（那是 `coord-commander tick`）、改写 mission 文件（那是 `mission_runtime.transition`）。

## 前置条件

1. 当前目录是仓库根，`coordination/` 存在；`python` 可用且 `PYTHONPATH=src`。
2. **本机不需要 LM Studio / Ollama**。服务关着、没有模型、显存为零时照常工作，
   不要用小模型来做心跳判断。
3. 目标 Agent 名字先从 `coordination/presence/agents/*.json` 与
   `coordination/clients/*.json` 的实际文件名确认。`valid_agent()` 只校验字符、
   **不校验成员**，写错会静默生成一个没人读的信箱。
4. 命令名与风险分级以 `docs/coordination/skill-manifest.json` 为准；派工契约见
   `schemas/dispatch-envelope-v1.schema.json`；结论口径见
   `docs/DELIVERY_TRUTH_MATRIX.zh-CN.md`。
5. 完整边界见 `docs/LONGRUN_MISSION_PULSE.zh-CN.md`。

## 步骤

1. **只读预览**（默认，不写任何文件）：

   ```
   PYTHONPATH=src python -m agent_evolution_protocol.coordination.mission_pulse --coord-root coordination
   ```

   抄 `scanned / actions / candidates / verified / rejected[].reason / queued / applied`。
   `scanned=0` 也要照抄——扫描器坏了与没有 mission 不能长得一样。

2. 查下面的「回执判定表」。只有 `candidates > 0` 且没有硬拒绝原因时，才允许走第 3 步。

3. 需要入队时才追加 `--apply`：

   ```
   PYTHONPATH=src python -m agent_evolution_protocol.coordination.mission_pulse --coord-root coordination --apply
   ```

   抄 `queued[].state`，只能是 `DEPOSITED`。**入队不是送达**。同一 `event_id` 重复运行不会
   产生第二单，不要为了「保险」再跑一次。

4. 真正发送由**既有心跳**负责，不由本卡自动执行：

   ```
   aep --coord-root coordination coord-commander tick
   ```

   这是 `irreversible`，先核对 `docs/coordination/skill-manifest.json` 的风险分级再跑。

5. 查回执（只读）：

   ```
   aep --coord-root coordination coord-delivery-status --to <target> --id <event-id>
   aep --coord-root coordination coord-delivery-audit
   ```

6. 只有 `VERIFIED` 能关单。其余状态继续等或升级，必要时用
   `coord-claim-renew` 续租、`coord-task-state` 写回阶段状态。

7. 周期触发：外部调度调用 `scripts/run-mission-pulse.ps1`（一次性、有界、退出即结束）。
   需要 OS 计划任务时，由**操作员显式**运行
   `scripts/register-mission-pulse-task.ps1 -Register`；不带 `-Register` 只打印计划，不改系统。

## 回执判定表

| 字段 | 取值 | 结论 | 下一步 |
|---|---|---|---|
| `scanned` | `0` | 没有 mission | 停止；不启动任何东西 |
| `candidates` | `0` | 本拍没有可派工阶段 | 查 `rejected[].reason` 后停止 |
| `rejected[].reason` | `ACTION_NOT_ASSIGNABLE` | 阶段在跑 / 失败 / 受阻 / 待交付 | 停止；不要重试 |
| `rejected[].reason` | `UNKNOWN_AGENT` / `NO_TARGET` / `INVALID_AGENT_NAME` | 目标不合法 | 停止，升级给人类 |
| `rejected[].reason` | `HUMAN_REQUIRED` / `WAITING_FOR_EVENT` / `TASK_TERMINAL` | 等人类或已终态 | 停止，**禁止自动接管** |
| `rejected[].reason` | `CLAIM_OWNER_MISMATCH` / `CLAIM_EXPIRED` / `MISSION_PAST_DEADLINE` | 租约或期限不允许 | 停止，升级给人类 |
| `rejected[].reason` | `ALREADY_QUEUED` | 已入队，幂等生效 | 停止 |
| `applied` | `false` | 只读预览，没有写 | 正常；要入队才加 `--apply` |
| `queued[].state` | `DEPOSITED` | **只入队，不算送达** | 继续走既有 `coord-commander tick` |
| `queued[].state` | 其他值 | 越界 | 停止并报告 |
| `models_loaded` / `processes_started` / `ui_touched` / `deployment_performed` / `system_tasks_registered` | 非 `false` | 越过硬边界 | 停止并报告 |
| 投递回执 | `DEPOSITED` / `MESSAGE_VISIBLE` / `AGENT_ACK` / `WORKING` / `EVIDENCE_RETURNED` | 都未关单 | 继续查回执 |
| 投递回执 | `VERIFIED` | **唯一可以关单的状态** | 结束 |

## 失败处置

- 一律三选一：**停止 / 延期 / 升级给人类**。禁止自由重试、换会话、绕过闸门。
- **禁止**让 LM Studio 或任何小模型参与心跳判断；禁止启动常驻 watcher、后台线程、
  模型 server；禁止由 agent 自己注册系统任务。
- **禁止**把 `DEPOSITED` 当送达，禁止对 `submit` / `upload` / `publish` / `delete` /
  `payment` 等高风险动作自动重试。写结果未知时标 `UNKNOWN`，不重放。
- 不要承诺 7x24 完全无人值守：`docs/NOVICE_LONGRUN_WORKFLOW.zh-CN.md` 明确写了
  mission planner、持久化 DAG runner 和通知策略仍未完成；OS 计划任务还需要登录会话。
- 只有「已收到」一句空 ACK 时，按
  `docs/coordination/EVIDENCE_BACKED_AGENT_HANDOFF.zh-CN.md` 要求补正文与校验摘要。

## 验收门

```
python -m pytest -q tests/test_mission_pulse.py tests/test_capability_cards.py
```

期望全绿。该测试守：零模型 / 零子进程、dry-run 零写入、全部闸门拒绝、
`--apply` 只产生 `DEPOSITED`、幂等、复算防漂移、触发脚本默认不注册系统任务。

## 边界

本卡片只负责「读 mission → 过闸门 → 可选入队」。它不新建调度器、不新增 schema、
不新增 `coord-*` 命令面、不改 mission 文件、不碰桌面、不加载模型、不注册系统任务。
定时器只负责触发只读命令，**定时器不是判断**。
