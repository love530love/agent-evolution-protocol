---
name: aep-succession-board
description: 只读聚合能力声明、额度信封、claim 进度、角色持有者与投递健康，回答「当前谁在指挥、谁可安全接手、缺什么」；所有接手建议 advisory-only，绝不自动认领角色、投递或标记 ACK。零写入、零模型、零网络。
min_agent_capability: cli
model_required: false
---

# aep-succession-board

额度/时间受限时，弱 agent 最常见的问题是「我不知道现在谁在指挥、哪个角色空着、
我能不能接手」。本卡给一条只读命令把现有事实聚合成一张态势板。**它不替代**
`command_handoff`（角色状态机）、`relay`（额度接力）、`capability_roster`
（能力声明）或 `collab_health`（在线健康）的任何判定，只做组合视图。

## 何时用

- 新 agent / 弱 agent 加入战局，需要先看清全局再决定接手什么；
- 主力 agent 撞到额度，需要判断哪个角色能被谁安全接管；
- 想核对「大家都在写代码」是否等于「指挥链已经可接手」（通常不等于）；
- 值守心跳需要一条可复算的进度/缺口快照。

**不适用于**：发送消息、唤醒/插话、真正认领角色、写 ACK、改订单、注册 OS 任务、
调用本地模型。本卡只读磁盘，不写协作区。

## 前置条件

- 有 Python 与仓库 `src/` 可导入；不需要桌面窗口、管理员权限、模型、网络、API Key。
- 有 `coordination/` 目录即可；目录不存在或某来源损坏时输出 `DEGRADED` + 命名缺口，
  不会猜一个「在线」或「有额度」出来。

## 步骤

1. **读态势板**（默认 JSON，可注入时间戳做确定性复算）：

   ```text
   python -m agent_evolution_protocol.coordination.succession_board \
     --coord-root coordination --at 2026-11-22T06:00:00+00:00
   ```

2. **读人类摘要**：把 `--format brief` 换上，得到角色/agent/缺口/建议四段式摘要。

3. **按回执判定表行动**：先看 `verdict` 与 `gaps`，再看 `takeover_advice`。
   建议里的 `dry_run_command` 只是投递预览，**不等于角色接手，也不等于 ACK**。

## 回执判定表

| 字段 | 结论 |
|---|---|
| `verdict=ACTIONABLE` | 至少有一个同时满足 fresh presence + ACTIVE 额度 + 能力匹配的接手者；仍需人类/授权 agent 复核 |
| `verdict=GAPS` | 有明确缺口（如 `ROLE_NOT_HELD`、`NO_CAPABILITY_DECLARATION`），不可宣称可接手 |
| `verdict=DEGRADED` | 某个来源不可读；只报告 `SOURCE_UNAVAILABLE:*`，不猜状态 |
| `roles[].coverage=UNCOVERED` | 该角色无 ACTIVE holder；不是「有人在做」就算持有 |
| `agents[].presence=stale` | 证据过期，**不是 offline**，也不得作为接手依据 |
| `agents[].quota=UNDECLARED/EXHAUSTED/EXPIRED` | 不得接手；自报额度只是证据，不是厂商余额证明 |
| `gaps` 中的 `ACK_PENDING` | 有投递待 ACK；信箱文件、`MESSAGE_VISIBLE` 都不算 ACK |
| `takeover_advice[].may_advance` | 恒 `false`；不得把建议喂给任何推进/认领函数 |
| `takeover_advice[].execution_started` | 恒 `false`；本卡从不执行 |
| `agents[].message_is_ack` | 恒 `false`；消息文本里出现校验串也不算原会话 ACK |

## 失败处置

- **禁止**把 `takeover_advice` 当成已交接；真实接手必须由 `command_handoff` /
  `relay` 的授权流程显式执行并留回执。
- **禁止**为了让看板好看而把 stale 改成 offline、把 UNDECLARED 改成 ACTIVE，
  或把 `may_advance` 改成 `true`。
- **禁止**用 `dry_run_command` 的预览结果宣称投递成功；投递不等于接单，接单不等于 ACK。
- 失败只允许三选一：**停止 / 延期 / 升级给人类或当前 holder**。

## 验收门

```text
python -m pytest -q tests/test_succession_board.py tests/test_capability_cards.py
```

全量回归本机须带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`。设计与真实读数见
`docs/SUCCESSION_SITUATION_BOARD.zh-CN.md`。本卡不新增 schema、不注册新的 `coord-*`
命令，因此不会与并行 agent 的 CLI 写入冲突。
