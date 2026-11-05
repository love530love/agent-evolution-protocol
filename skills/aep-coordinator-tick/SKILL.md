---
name: aep-coordinator-tick
description: 把三条既有单步（回表截止、mission 入队、真发）接成一次有界的居中协调一拍；零模型、默认只读预览，按回执判定是否允许 --apply 与是否算送达。
min_agent_capability: cli
model_required: false
---

# aep-coordinator-tick

**零模型**、**不是第二套调度器**。本卡只负责「按固定顺序调用三个已经存在的单步」，
不判断任务该不该做，也不自己发消息、不启动进程、不注册计划任务。

## 何时用

- 需要一次「居中协调的一拍」：先结清回表与截止，再把持久化的 mission 阶段入队，最后才真发。
- 需要一份能抄进回执的统一报告，说明每一步调了什么、跳过什么、为什么停下。
- 新 Agent 不知道该调 `campaign_tick`、`mission_pulse` 还是 `federation/supervisor.step` 时。

**不适用于**：新建 mission、改写 mission 文件、评审/晋升、自动改源码、真正判断某个 Agent 是否接单、
启动常驻 watcher、注册 OS 计划任务、把 `DEPOSITED` 当作送达。

## 前置条件

- 有 `coordination` 运行时目录（真协作区或临时沙盒均可）。
- Python 能导入 `agent_evolution_protocol`（脚本会设置 `PYTHONPATH=<repo>/src`）。
- 默认不需要 federation DAG 上下文；只有同时给 `--kernel-root`、`--workspace`、`--agents`
  才会走 DAG 路径。
- 模型、LM Studio、Ollama、桌面窗口都不需要。定时器不是判断，本卡不依赖任何定时器。

## 步骤

1. **先只读预览**（默认，零写入）：

   ```text
   python -m agent_evolution_protocol.coordination.coordinator_tick --coord-root coordination
   ```

   抄回执：`applied=false`、`writes_performed=0`、`preview.mission.scanned`、`preview.campaign.missions`、
   `preview.presence`、`preview.commander.orders`、`preview.next_due_at`、`plan` 四行。

2. **按回执判定表判断**是否允许 `--apply`。任何 `unavailable` 字段都要先查清，
   不要靠猜补齐。

3. **确认后才执行**（唯一写入口）：

   ```text
   python -m agent_evolution_protocol.coordination.coordinator_tick --coord-root coordination --apply
   ```

   或用有界脚本：`scripts/run-coordinator-tick.ps1 -Apply`。
   固定顺序：`campaign_tick.tick` → `mission_pulse.pulse(apply=True)` → **恰好一条**投递路径
   （有 DAG 上下文用 `federation/supervisor.step`，否则用 `commander.tick`）。
   **不回退**：federation 步骤失败时绝不改调 `commander.tick`，因为 `step` 内部可能已经发过，
   再发一次就是重复投递。

4. **查真投递回执**：本卡只保证「订单被入队/被推进」，不保证送达。
   按 `docs/DELIVERY_TRUTH_MATRIX.zh-CN.md` 查 `coord-delivery-status`、
   `AGENT_ACK`、`EVIDENCE_RETURNED`。**只有 `VERIFIED` 能关单**。

5. **不要自己关单**，不要执行 `improvement.advance/validate`，不要把 `DEPOSITED` 写成完成。
   是否周期触发、多久一拍，由操作员或 OS 计划任务决定；本卡不注册任何系统任务。

## 回执判定表

| 回执字段 | 结论 |
|---|---|
| `applied=false` | 只读预览，**没有写任何文件**；不能宣称已派工 |
| `preview.mission.scanned` | 扫到多少 mission；为 0 要能区分「没有任务」与「路径写错」 |
| `preview.*.unavailable` | 某个只读源损坏；先查原因，不要继续 `--apply` |
| `plan[].callable` | 将要调用的**既有**单步；出现新名字说明接线被改坏了 |
| `skipped[].reason=FEDERATION_CONTEXT_MISSING` | DAG 路径**没跑过**，已明确标记；走的是 `commander.tick` |
| `halted=SETTLE_FAILED` / `MISSION_FAILED` | 前置失败，已 fail-closed 停止，**没有真发** |
| `dispatch_path=federation_step` | 走 DAG 一拍（内部已含 `commander.tick`），绝不再单独调一次 |
| `dispatch_path=commander_tick` / `none` | 只走订单一拍 / 一拍都没发 |
| `stages[].status=failed` | 该步失败；`stages[].error` 是原因，失败不是成功 |
| `queued[].state=DEPOSITED` | 只是**入队**，不是送达；真发送由 `coord-commander tick` 负责 |
| `models_loaded=0`、`processes_started=0`、`ui_touched=false`、`deployment_performed=false`、`system_tasks_registered=false` | 硬边界；任一变成真值即越界 |
| `AGENT_ACK` / `WORKING` / `EVIDENCE_RETURNED` | 接单证据（仍不是关单） |
| `VERIFIED` | **唯一**允许关单的结论 |

`DEPOSITED` / `WINDOW_FOUND` / `INPUT_SUBMITTED` / `MESSAGE_VISIBLE` 都不算送达；
`WAKE_QUEUED_UNVERIFIED` / `RELAY_QUEUED_UNVERIFIED` / `MAILBOX_ONLY` / `REJECTED` 都不算接单。

## 失败处置

- **禁止**让模型参与这一拍的判断：它只回答可复算的确定事实，概率模型没有「不知道」输出。
- **禁止**启动常驻 watcher、后台线程、model server、Postgres、代理服务或 OS 计划任务。
- **禁止**在 `halted` 非空时手工重放 `--apply`；先修前置状态，再从步骤 1 重新预览。
- **禁止**在 federation 失败后「补一发」；重复投递比不发更糟。
- **禁止**把入队当送达，禁止用 `VERIFIED` 之外的状态关单。
- 失败只允许三选一：**停止 / 延期 / 升级给人类**。空 ACK 时按
  `docs/coordination/EVIDENCE_BACKED_AGENT_HANDOFF.zh-CN.md` 要求补分析正文、摘要与 SHA-256。

## 验收门

```text
python -m pytest -q tests/test_coordinator_tick.py tests/test_capability_cards.py
```

全量回归本机须带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`。边界与「本次未做」见
`docs/COORDINATOR_TICK.zh-CN.md`；愿景基线见 `docs/COORDINATOR_VISION_EVIDENCE.zh-CN.md`。
