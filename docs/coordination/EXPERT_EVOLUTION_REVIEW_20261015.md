# 自治协作迭代审议与交接（2026-10-15）

本轮由治理、激励、对抗评审三个独立 subagent 提供实现与专项验证，主协调者完成命令行整合。审议对象是 AEP 的本地协作契约，借鉴 3D 项目的角色、干预和激励方法。

## 审议结论与取舍

| 专家方向 | 提出的问题 | 本轮实现 | 仍需验证 |
| --- | --- | --- | --- |
| 治理 | 角色名称可能被当成权限，干预可能静默改变规则 | 可发现的角色契约、L0–L3 分级、证据摘要、独立审阅者；高风险提案隔离记录 | 接到真实执行器前的权限校验、审议通过后的采纳流程 |
| 激励 | 自报贡献、重复证据与不同文件名可能刷分 | 独立裁定、事件与证据哈希、重叠证据去重、AP/RP 分账和慢变声誉 | 真实身份绑定、官方证据来源、串通审阅检测 |
| 鲶鱼 | 一个角色包办挑战与裁决，容易自证 | 设想→反证→复现→偏差审计→裁决，独立 Agent 身份与前序证据绑定 | 实际队友在原会话完成一轮评审的端到端验收 |

角色身份目前由本地调用者声明，尚无身份认证。旧 scoreboard 保留活动统计含义；资源分配应参考独立裁定后的 incentive-board，不能把旧分数直接当权限。完整五角色评审链需要五个不同身份；队伍不足时状态保持不完整，不伪造独立审议。

## 接入入口

在 PowerShell 7 设置本项目 `src` 为 PYTHONPATH 后，统一入口为 `python -m agent_evolution_protocol --coord-root <协作目录>`：

- `coord-governance roles`：发现职责；`intervene --request-file <JSON>`：登记干预；`history`：读取历史。
- `coord-adversarial review --task <任务> --submission <提交> --agent <身份> --role <角色> --review-file <JSON>`：按顺序提交评审；同参数的 `status` 查询缺口。
- `coord-legion adjudicate --event-id <贡献事件> --reviewer <独立身份> --verdict accepted --evidence <原事件引用的协作目录内文件> --note <理由>`：冻结裁定。
- `coord-legion incentive-board`：查询 AP、RP、声誉、待审与失效条目。

具体 JSON 字段见 [治理契约](ROLE_AND_INTERVENTION_GOVERNANCE.md)、[多角色鲶鱼](MULTI_ROLE_CATFISH.md)和[军团激励契约](LEGION_COMBAT_PACT_20261013.md)。这些命令只形成可检查的本地记录；现有上传锁、回表核验和投递 ACK 机制继续负责执行证据。

## 下一轮目标

将独立评审结论接入冻结候选门禁；增加审议版本和明确采纳记录；用真实任务验证是否减少重复错误与无效上传，再考虑按经核验的贡献调整资源。唤醒和即时送达仍需逐客户端实机验收，此轮角色与评分代码不能替代该验收。
