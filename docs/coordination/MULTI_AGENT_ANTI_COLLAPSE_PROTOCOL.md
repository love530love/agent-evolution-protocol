# 红皇后—鲶鱼—创造性破坏协议 v1.1

状态：`SHADOW / HUMAN_HOLD`。本协议先用于离线回放与下一题旁路试点；它不授权自动调用模型、占用 GPU、修改候选源码或向真实平台提交。

## 1. 目标与三种动力

- **红皇后压力**：只接受有来源、时间和置信度的外部变化，迫使系统持续校准；不得虚构危机制造永久紧急状态。
- **鲶鱼扰动**：逻辑常驻、计算上事件唤醒。它负责攻击共识、提出结构性替代方案，并拥有一张有预算上限的证伪票。
- **创造性破坏**：候选不靠投票、资历或叙事胜出，而由预注册、同预算、可复现的实验淘汰和晋级。

协作室是协调平面，不是共享大脑。规则、安全、基线 SHA、预算和已验证结果立即共享；候选假设、推理和参数搜索在 commit 前保持隔离。

## 2. 角色、权力与职责分离

| 角色 | 权力 | 禁区 |
|---|---|---|
| Leader / Accountable Owner | 定目标、预算、期限、分工和执行顺序；最终集成 | 无权宣布技术真理；不得兼任唯一裁判或唯一鲶鱼 |
| Exploration Cell | 形成独立、可证伪候选 | commit 前不得读取竞争路线；污染后须标记 |
| Evolutionary Challenger / Catfish | 挑战假设、提出替代下注；每轮一张不可被多数取消的小额证伪票 | 不得绕过安全、预算或无限阻塞；轮换 Agent/模型/提示策略 |
| External Threat Monitor | 发布带来源的外部信号 | 不直接指挥执行；传闻不得触发高成本动作 |
| Evaluator | 匿名、同环境运行预注册指标 | 不参与候选设计，不做叙事性美化 |
| Integrator | 每轮集成一个主胜者，可保留一个结构差异最大的备选 | 不得把多个未验证方案平均融合 |
| Safety Guardian | 对合规、安全、不可逆破坏、数据污染硬暂停 | 技术分歧只能发证据挑战，不能伪装为安全否决 |
| Human | 批准不可逆操作、真实平台高成本消耗、规则例外与争议升级 | — |

同一 Agent 同一轮不得同时担任 `candidate author + sole evaluator + final approver`。每个任务恰有一名当前 Leader。

## 3. 路由：什么时候需要多少“脑”

| 模式 | 适用条件 | 默认动作 |
|---|---|---|
| SOLO | 低风险、可逆、答案明确 | 单 Agent 执行并自检 |
| PERSONA_PANEL | 需要快速列举风险但不要求独立证据 | 同一模型多视角；结果只算一组相关意见 |
| SUBAGENTS | 可拆分、并行只读审查或独立产物 | 临时并行，结束即释放；不能冒充外部独立证据 |
| FEDERATED_CELLS | 高不确定、高价值、路线易趋同 | 不同 Agent/上下文隔离探索，commit 后统一揭示 |

升级条件包括：高风险不可逆动作、预期路线不少于 3 条、连续两次同类失败、模型与平台结果相反，或用户明确要求多专家审议。

## 4. 状态机与硬规则

任务级：

```text
INTAKE -> ROUTED -> EXPLORING -> COMMIT_LOCKED -> REVEALED
-> FALSIFICATION -> TOURNAMENT -> SELECTED -> INTEGRATING
-> VERIFIED -> RELEASED
```

候选级：

```text
DRAFT_PRIVATE -> COMMITTED -> REVEALED -> ELIGIBILITY_CHECKED
-> CHEAP_FALSIFICATION -> SURVIVED -> PAIRED_BENCHMARK
-> PROMOTED | ELIMINATED | INCONCLUSIVE
```

允许终止态：`REJECTED_RULE`、`ELIMINATED_EVIDENCE`、`STOPPED_BUDGET`、`STOPPED_NO_INFO`、`SUPERSEDED`、`HUMAN_HOLD`、`INCIDENT_HOLD`。

硬规则：

1. 不得跳过 commit 直接进入平台实验。
2. 工件或预测变化产生新 candidate ID，不覆盖旧记录。
3. `ACK`、`READ`、`ACCEPTED`、`RUNNING`、`DONE`、`VERIFIED`、`RELEASED` 是不同事件。
4. 每次状态转换记录 actor、时间、原因、输入/输出 SHA 与预算变化。
5. 所有 cell 完成 commit 后才能 reveal；提前接触竞争方案者标记 `contaminated=true`，不再算独立支持证据。
6. commit-reveal 只提供审计完整性，不构成文件访问安全边界；真实隔离依赖独立对话、工作树与访问纪律。

## 5. 信息平面

- `PUBLIC_FACT`：官方规则、环境、基线、SHA、预算余额、已确认结果；立即广播。
- `SAFETY_ALERT`：违规、泄密、污染、不可逆风险；立即广播并可暂停。
- `CELL_PRIVATE_HYPOTHESIS`：commit 前仅本 cell 可见。
- `COMMITTED_SEALED`：公开 candidate ID、哈希、时间和预算占位，不公开正文。
- `EVALUATOR_ONLY`：隐藏用例、匿名映射与裁判细节。
- `REVEALED_CANDIDATE`：截止后统一公开。
- `EXTERNAL_SIGNAL`：必须带来源、时间、TTL、置信度和廉价确认办法。
- `DECISION`：必须附证据与适用条款。
- `INSPIRATION_POOL`：无证伪办法的灵感可保留，但不得阻塞主流程。

## 6. 鲶鱼触发与挑战格式

以下事件生成 `CHALLENGE_REQUEST`，只入队，不自动调用高成本模型或平台：

- 有效独立假设少于 3，或候选结构相似度超过 0.75；
- 连续两次同类失败且没有信息增益；
- 所有 cell 异常快速达成一致；
- 主路线连续两轮占用超过 60% 预算；
- 本地预测与平台结果方向相反；
- 协调消息增长超过 50%，独立假设数不增长；
- Leader 连续否决两个非共识候选；
- 最终冻结、真实提交或发布之前；
- 外部威胁升至 `HIGH/CRITICAL`；
- 用户发送 `@鲶鱼:` 或 `@challenge:`。

有效挑战必须包含：

```yaml
challenged_assumption:
alternative_hypothesis:
structural_difference:
predicted_signature:
cheapest_falsification:
max_budget:
stop_condition:
```

缺字段的想法只进入灵感池。鲶鱼的证伪票保证一次小额实验，不保证路线续命。

## 7. 外部威胁登记

外部信号至少记录：

```yaml
source:
observed_at:
freshness_ttl:
confidence: verified | inferred | rumor
impact: low | medium | high | critical
affected_assumptions:
cheapest_confirmation:
recommended_response:
response_deadline:
```

- `verified + critical`：暂停受影响动作，唤醒 Leader、Safety、Catfish。
- `verified + high`：开一条威胁响应候选，使用预留应急预算。
- `inferred`：先做廉价确认。
- `rumor`：只观察，不消耗真实平台额度。

每轮预留 15%–20% 预算用于外部危机与恢复；不得借“危机”反复绕过正常门禁。

## 8. 实验市场与 Leader 问责

候选先过合规、SHA、唯一假设、停止条件、复现命令、污染披露和预算门槛。竞争顺序固定为：正确性/合规 → 根因 → 真实平台覆盖 → 主指标增益 → 稳健性 → 预测校准 → 信息增益/成本 → 复现性 → 复杂度。

每个合格候选先获得一次最便宜证伪；淘汰明确失败和无信息者；之后 successive halving；决赛必须匿名、配对、同环境、同预算。差异落在误差边界内时标记 `INCONCLUSIVE`。

Leader 每轮预注册成功指标、预算、期限、模式选择、资源分配和人工批准点；结束时提交：

```yaml
decision:
alternatives_considered:
evidence_used:
dissent_preserved:
budget_spent:
prediction_vs_result:
what_would_reverse_decision:
next_review_at:
```

连续两轮预测失准、协调成本超过 20%、单路线占用超过 60% 且无领先证据、SHA 绑定率低于 100% 或重复无信息失败，依次触发解释、限预算、联合签字、临时替换；安全事故可直接暂停。

## 9. 影子试点和成功判据

先用一题历史记录回放，再在下一题旁路运行，不影响现有正式流程。建议指标：

- 工件—结果 SHA 绑定率 = 100%；
- 有效实验中信息增益实验占比 >= 80%；
- 可归因提交 >= 90%；
- 协调开销 <= 总成本 20%；
- 首次有效信号时间不劣于旧流程；
- 至少 3 个独立假设，且记录“非共识候选胜出率”；
- 普通消息零模型唤醒，只有显式事件触发执行。

未完成历史回放、旁路试点和人工复核前保持 `HUMAN_HOLD`。协议本身也必须接受创造性破坏：若不能改善首次有效信号、独立假设数或最终成绩，应缩减或替换，而不是继续增加委员会层级。

## 10. Institutional Kernel v0.2

本地影子运行器现将上述制度落实为可审计事件：事件以 SHA256 前向链封存；挑战、威胁、Leader 决策和 HOLD 均使用结构化卡片；预算与工件 SHA 在结果写入时强制校验。任何 hash-chain 审计失败都应触发 `INCIDENT_HOLD`，不得用叙事性说明覆盖。

## 11. Cluster Recovery Kernel v0.3

v0.3 解决的不是候选质量，而是集群在卡死、断联、界面自动化失效和会话漂移时
如何安全继续：状态拆为 intent、task、executor、channel 四轴；写任务、上传意图、
浏览器会话分别持有带 epoch 的租约；外部副作用以稳定幂等键记账；结果未知时先
对账；接管前先冻结副作用并写 checkpoint。

延续执行默认使用原会话精确 ID。Codex 投递到既有 thread，Hermes 恢复既有
session；“同项目”不再作为“有相同上下文”的替代证据。无可信会话绑定时默认
`HANDOFF_REQUIRED`，禁止静默创建新聊天。新会话必须由显式授权触发，并携带
可验证交接包。详见 `README.md` 与 `cluster_kernel.py`。
