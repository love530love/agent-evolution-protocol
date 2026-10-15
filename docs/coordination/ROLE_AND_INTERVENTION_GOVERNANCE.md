# 多角色鲶鱼与干预治理（试运行契约）

借鉴 3D 项目的角色登记和 L0–L3 干预分级；此处只移植治理结构，不移植其彩票指标或模型结论。实现见 `coordination/governance.py`。

## 角色分离

`orchestrator` 负责组织，不享有单独裁决权。`ideator` 提异路，`catfish` 寻找反例，`bias_auditor` 检查挑选性汇报，`replicator` 独立复现，`referee` 裁决证据，`gatekeeper` 检查对外表述，`archivist` 整理记忆。注册表只说明角色可做什么，不代表已有在线 Agent、已赋权或已 ACK。

发起者不能充当自己的审阅者；审阅者必须是不同的身份。不能用重复身份凑人数。角色声明、LLM 意见、模拟测试都不等于独立实机证据。

## 干预等级

| 等级 | 示例 | 写入后状态 |
| --- | --- | --- |
| L0 | 只读分析 | `RECORDED_READ_ONLY` |
| L1 | 普通实现提案 | `REVIEW_REQUIRED` |
| L2 | 指挥、指标、证据语义、角色或裁决变更 | `QUARANTINED` |
| L3 | 宪章和安全边界覆盖 | `QUARANTINED` |

L2/L3 必须提供存在的证据文件；提交时记录 SHA-256。每条提案独占一个记录文件，不覆盖旧提案。记录不是防篡改账本，也不自动应用变更；正式采纳还需独立审阅、风险相称的授权、测试和回滚演练。未知干预类型、非 JSON 数据、自审或无证据的高风险请求拒收。

调用接口：`roles()`、`assess_intervention(request)`、`submit_intervention(root, request)`、`read_interventions(root)`。请求须含 `type`、`by`、`target`、`rationale`，可含 `evidence_paths`、`reviewers`、声明式 `after`。不要把 `reviewers` 字段当成他们已审阅或已同意的证明；它只是提议的审阅安排。

CLI 已接入 `coord-governance roles|intervene|history`。其中 `intervene --request-file <JSON>` 只创建提案；采纳与执行仍需后续流程。
