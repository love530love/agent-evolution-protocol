# 额度中断故障注入演练取证（2026-11-23）

## 边界先行

这是一次**用真实状态机函数、在隔离目录**跑的故障注入演练。它证明的是：
在"指挥者额度耗尽"这一注入条件下，项目的 `command_handoff` + `capability_bridge`
能按设计完成 `ACTIVE → YIELDED → TAKING_OVER → ACTIVE` 的幂等、可复核接手。

它**不证明**：任何厂商 agent 真的额度耗尽、真的收到消息、真的回 ACK、
真的接管了一个真实任务。演练里的人名（`holder_alpha` / `weak_beta` /
`successor_gamma`）是夹具，不是协作区登记身份；证据串以 `fault-injection-drill:`
开头，不是 agent 自报。任何把它升级为"已完成跨厂商接力"的说法都是伪造。

## 复算命令

```bash
cd K:\PythonProjects5\agent-evolution-protocol
.tools/agent-runtime/Scripts/python.exe -m agent_evolution_protocol.federation.capacity_drill
# 或定向到固定目录（保留现场供审计）
.tools/agent-runtime/Scripts/python.exe -c \
  "from pathlib import Path; from agent_evolution_protocol.federation.capacity_drill import run; \
   import json; print(json.dumps(run(Path('coordination/_drill_audit')), ensure_ascii=False, indent=2))"
```

## 注入序列与观测

| 步骤 | 真实调用 | 观测 |
| --- | --- | --- |
| 1 初始化 | `claim(commander, capability 由 bridge 从 desktop-ui 映射)` | ACTIVE，唯一 holder |
| 2 弱候选报名 | `eligibility(commander, ["research"])` → `volunteer` | `eligible=false`，记入候选但不可接手 |
| 3 合格候选报名 | `eligibility(commander, ["desktop-ui"])` → `volunteer` | `eligible=true` |
| 4 注入额度耗尽 | `yield_role(signal="quota_exhausted")` | `YIELDED`，`takeover_eligible=true` |
| 5 幂等重放 yield | 同一 `yield_role` 再调一次 | `idempotent=true`，事件数不增 |
| 6 弱候选抢接 | `takeover(to_agent="weak_beta")` | 抛 `ValueError`，**fail-closed** |
| 7 合格候选接手 | `takeover(to_agent="successor_gamma")` | ACTIVE，transition 三段完整 |
| 8 幂等重放 takeover | 同一 `takeover` 再调一次 | `idempotent=true`，handoff 记录仍 1 条 |
| 9 状态与链校验 | `status()` | `chain_valid=true`、单 ACTIVE、状态已落盘 |

## 实测结果（2026-11-23，本机 Python 3.13.9）

`verdict=PASS`；14 项 checks 全部 true：
`initial_claim_active, weak_ineligible, weak_recorded_ineligible, successor_eligible,
yielded_on_quota, yield_idempotent, weak_takeover_refused, successor_active,
transition_recorded, takeover_idempotent, chain_valid, single_active_holder,
one_handoff_record, state_persisted`。
`handoff_id=CH-e9816d02c56514b9`，`event_count=5`（replay 未产生额外事件），
`final_holder=successor_gamma`。守线：`tests/test_capacity_drill.py` 5 项 +
`test_command_handoff.py` + `test_handoff_capability_bridge.py` 共 28 passed。

## 由演练暴露的真实边界

1. **演练证明的是状态机正确，不是人在线**。真实接力仍卡在三判据：
   presence fresh 自报 + 可消费通道 + 原会话 ACK。本演练不绕过任何一条。
2. **额度信号是注入的，不是检测的**。`command_handoff` 不接厂商额度 API；
   真实 `quota_exhausted` 必须由实际执行者上报，未知保持未知。
3. **在途副作用未覆盖**。状态机幂等 ≠ 桌面输入幂等；已在途的外部操作
   （消息已发一半、订单已输入）须按 `HANDOFF_OWNERSHIP` 的租约/检查点规则单独核验，
   本演练不声称"执行取消后的自动恢复"。
4. **用户最新指令优先级**：本演练只验证接手状态链；真实场景下冲突指令的优先级
   仍由 operators 显式裁定，未做成自动覆盖。

## 结论

故障注入演练**通过**，可作为"额度中断 → 合格接替者读状态 → 续做 → 产物验收"
这条链路的状态机侧证据。跨厂商真实接力的最后一公里（真实额度信号、真实 ACK、
真实在途副作用）仍未验证，按项目纪律如实记为未完成。
