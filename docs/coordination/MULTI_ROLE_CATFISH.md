# 多角色鲶鱼证据链

一个 Agent 提出异路，不等于这个异路已经被反证、复现或准许投递。`joint_attack` 的原有 `cross_platform + catfish` 会诊仍然有效；需要更严格的独立审议时，使用五环链：

1. `ideator` 提出可证伪假设、预期签名、最便宜的实验和止损条件。
2. `falsifier` 提出竞争解释与能区分两者的实验。
3. `replicator` 按记录的协议独立复现，结果为 `reproduced`、`refuted` 或 `inconclusive`。
4. `bias_auditor` 查选择性汇报、遗漏形状／芯片／失败样本。
5. `referee` 给出 `advance`、`hold` 或 `reject`，并列出残余风险。

五个角色必须由五名不同的 Agent 担任；主攻者可充当设想者，但不得再任后四个复核角色。每环附存在的本地证据文件，并绑定案例与前一环的 SHA-256。先前评审或证据被改动、丢失，整条后续链的状态即失效。评审文件只增写，不覆盖；新证据应开新案例或进入另一次可追溯迭代，不可悄悄改旧结论。

代码入口：`submit_adversarial_review(root, task=..., submission=..., agent=..., role=..., review_file=...)` 和只读的 `adversarial_status(...)`。评审文件是 JSON，需有 `evidence_paths` 非空数组，以及各角色字段：

| 角色 | 必填字段 |
| --- | --- |
| ideator | `hypothesis`, `predicted_signature`, `cheapest_test`, `stop_condition` |
| falsifier | `counter_hypothesis`, `discriminating_test`, `predicted_signature`, `stop_condition` |
| replicator | `protocol`, `observed_result`, `reproduction_status` |
| bias_auditor | `selection_bias`, `missing_cases`, `audit_conclusion` |
| referee | `verdict`, `rationale`, `residual_risk` |

这是本地审议与自进化的证据约束，不会启动模型、唤醒队友或提交包。即使五环完成且裁判选择 `advance`，`upload_ready` 仍为 `false`；官方回表、包审查、额度和上传锁继续独立把关。不得以积分、角色数量或一致票数替代外部验收。
