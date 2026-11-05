# 能力卡统一规范

## 为什么要有这张规范

接入的 Agent 提供的模型能力大小不一。有些只有文件读写能力，无法可靠完成窗口唤醒、界面判断或多步推理。**能力不能依赖 Agent 自己有多聪明**——把已实测通过的能力固化成「照着做、查表判断」的卡片，弱 Agent 也能得出和强 Agent 相同的结论。

一句话原则：**卡片负责判断，Agent 只负责执行和抄回执字段。**

## 不新增标准

本规范只定义 Markdown 组织方式，不定义新 schema、不新增命令面：

| 需要什么 | 复用哪个既有载体 |
|---|---|
| 命令名、参数、风险分级 | `docs/coordination/skill-manifest.json`（由 `build_parser()` 生成，**不可手改**） |
| 派工契约字段 | `schemas/dispatch-envelope-v1.schema.json`：`event_id / preconditions / acceptance / evidence_required / retry_policy / stop_conditions` |
| 回执 → 结论 | `docs/DELIVERY_TRUTH_MATRIX.zh-CN.md`（只有 `VERIFIED` 能关单） |
| 能力包结构 | `schemas/agent-capability-bundle-v1.schema.json`：`skills[] / fallbacks[] / acceptance{}` |
| 给 Agent 读的格式 | `skills/aep-agent-onboarding/SKILL.md`：YAML frontmatter + Markdown 步骤清单 |

## 卡片格式

每个能力一个目录 `skills/<能力名>/SKILL.md`。frontmatter 必需字段：

```yaml
---
name: <与目录名一致>
description: <一句话，说明何时使用>
min_agent_capability: file-only | cli | desktop-ui
model_required: false
---
```

正文**必需且顺序固定**的六个小节：

1. `## 何时用` — 触发条件，可机械判断。
2. `## 前置条件` — 路径/权限/状态，逐条可核对。
3. `## 步骤` — 编号列表。每步只能是「执行一条确定的命令」或「读取并抄下一个回执字段」。**禁止**「根据情况分析」「综合判断」「酌情处理」。
4. `## 回执判定表` — 表格：`字段 / 取值 / 结论 / 下一步`。弱 Agent 只需查表，不需要推理。
5. `## 失败处置` — 每个失败分支只能是「停止」「延期」「升级给人类」三者之一。**禁止**自由重试、换会话、绕过闸门。
6. `## 验收门` — 一条可复算的命令 + 期望输出，用来证明这次执行没走偏。

## 硬性规则

- **卡片不得要求模型能力**。`model_required` 默认 `false`；若确需模型，必须写明「模型缺席时照样完成的降级路径」。
- **卡片里出现的 `coord-*` 命令名必须存在于 `skill-manifest.json`**，由 `tests/test_capability_cards.py` 守线。
- **引用的文档路径必须存在**，同一测试守线。
- **回执字段必须是代码里真实输出的字段**，不许在卡片里发明字段名。
- 报告「零发现」的卡片必须同时给出扫描量；扫描器坏了与机器干净不能长得一样。
- 不得在卡片里写「测试通过即晋级」。测试通过只是 `VALIDATED`，晋升仍需独立 reviewer 与回滚点。
- 卡片里的结论性描述若与实测冲突，以实测回执为准并**同步修文档**，不得让两张卡或文档长期并存两种说法。

## 当前能力卡

| 卡片 | 最低能力 | 是否需要模型 | 覆盖能力 |
|---|---|---|---|
| `aep-agent-onboarding` | cli | 否 | 会话绑定、协作、基础唤醒规范（散文规范，非执行清单） |
| `aep-start-dispatch` | file-only | 否 | **新 Agent 从零接入 → 认领 → 派工 → 回执判定** |
| `aep-insight-loop` | cli | 否（可选随用随走） | 自动发现问题 + 自动审计留痕 |
| `aep-desktop-wake` | file-only | 否 | 唤醒/派工/忙碌态插话（含 Qoder 0.5.3 实测序列） |
| `aep-mission-pulse` | cli | 否 | 长任务心跳接合：mission 阶段 → 既有 commander 队列，默认只读预览 |
| `aep-coordinator-tick` | cli | 否 | 居中协调一拍：把三条既有单步接成固定顺序，默认只读预览，投递路径严格二选一 |

新 Agent 要开始调度任务，从 `aep-start-dispatch` 起步；它给出 `aep join` 的最小序列，
并明确「只有 `VERIFIED` 能关单」。`aep-agent-onboarding` 是较早的散文规范，
用于理解纪律，不作为弱 Agent 的逐步执行清单。

`min_agent_capability: file-only` 的卡片必须提供一条「只写文件」的路径，不能要求命令行。

自动发现/自动审计这条能力的完整边界（自动到哪一步、不自动到哪一步、模型为何可选）见
`docs/AUTONOMOUS_INSIGHT_LOOP.zh-CN.md`。

长任务心跳的完整边界（显存实测、既有心跳清单、mission 断口、OS 触发归谁、为何不用小模型）见
`docs/LONGRUN_MISSION_PULSE.zh-CN.md`。**心跳不需要模型**：定时器只触发只读命令，判断由规则完成。

居中协调一拍的完整边界（三条既有单步、固定顺序、投递路径互斥、失败不回退、触发归操作员）见
`docs/COORDINATOR_TICK.zh-CN.md`。**接线不是新建调度器**：默认只读，`--apply` 才按既有函数顺序执行；
`federation/supervisor.step()` 内部已经调过 `commander.tick()`，所以两条投递路径必须严格二选一。
