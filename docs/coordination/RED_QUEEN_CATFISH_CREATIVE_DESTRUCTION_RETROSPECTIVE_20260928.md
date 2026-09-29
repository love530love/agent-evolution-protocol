# 红皇后—鲶鱼—创造性破坏机制：总结、复盘与迭代回溯

更新时间：2026-09-28（Asia/Shanghai）  
适用范围：`K:\PythonProjects5\FlagGems-sglang\competition\coordination`  
当前成熟度：`L2 — 工具可运行，跨代理通信已使用，治理闭环尚未完成影子验收`  
控制状态：`SHADOW / HUMAN_HOLD`

## 0. 执行摘要

这套机制解决的是两个不同层次的问题：

1. **通信问题**：不同 Agent 的消息互相看不到、无法即时传递、休眠时不能被可靠唤醒。
2. **认知问题**：通信打通后，各 Agent 因共享上下文、相互模仿和过早共识而趋于平庸，形成“信息更多、独立判断更少”的协作塌缩。

系统因此形成两层架构：

- **协调平面**：文件邮箱、ACK、任务认领、实时 SSE 时间线和显式唤醒。目标是可靠送达、责任可追踪、静默时零模型轮询。
- **认知竞争平面**：隔离探索、commit–reveal、鲶鱼挑战、外部威胁登记、廉价证伪、successive halving 和 Leader 问责。目标是保留差异，让证据而不是投票选择路线。

截至本次复盘：协调平面已经发生真实的 Codex、WorkBuddy、Qoder 等多方消息交换；认知竞争平面已形成协议、模板、离线 CLI 和自动测试，但尚未完成一次真实多方回合。因此不能宣称整个体系已经全面落地。

## 1. 为什么原始多 Agent 协作会“越协作越平庸”

复盘中识别出六个主要原因：

1. **信息污染**：探索者过早看到他人方案，独立搜索变成同一路线的语言改写。
2. **权威锚定**：Leader 或第一个发言者的方案成为默认答案，后续 Agent 只做局部修补。
3. **委员会平均化**：为获得表面一致，把结构不同的方案折中拼接，反而失去每条路线的有效机制。
4. **身份与证据混淆**：把“多个模型都同意”误当作独立证据；同模型、同上下文、同提示变体高度相关。
5. **沟通成本吞噬探索预算**：消息数量增加，但有效假设数量没有增长。
6. **没有淘汰机制**：反对意见可以无限延长，失败路线也可以用新叙事续命。

由此得到核心判断：**协作总线只能负责同步事实和协调行动，不能把所有推理实时广播成一个共享大脑。**

## 2. 演进回溯

### V0：人工转述和各自孤岛

早期状态：Codex 与 WorkBuddy 等 Agent 各自在独立对话中工作，彼此不能直接读取对方回复。用户承担人工搬运、监督和确认工作。

主要问题：

- 延迟高且容易遗漏；
- “已发送”“已读”“接受”“执行完成”混为一谈；
- Agent 无法证明对方收到；
- 休眠 Agent 不能通过普通消息自动继续执行。

### V1：可审计文件邮箱

建立 `bridge.py`，引入：

- 每 Agent 独立 inbox；
- 原子 JSON 消息；
- ACK 回执；
- `claim/release` 路径与任务认领；
- 文件系统作为离线可恢复的审计底账。

这一阶段解决“消息是否存在、谁拥有任务、谁确认已读”，但仍需主动查收，实时性有限。

### V2：事件驱动实时协作室

2026-09-22 建立 `realtime_hub.py`、`realtime_hook.py` 和本地页面：

- 仅监听 `127.0.0.1:8765`；
- SSE 推送共享时间线；
- 普通消息只进入时间线，不启动模型；
- 只有 `/api/wake` 且 `execute=true` 或严格唤醒暗号才启动一次独立 worker；
- 严格暗号包括 `@唤醒 codex:`、`@唤醒 workbuddy:` 及英文形式；
- `bridge.py` 继续作为底账，实时镜像失败不阻塞文件消息。

这一设计贯彻用户要求：**静默状态下不进行无意义轮询，不消耗模型 token。**

### V2.1：Hook “假成功”故障与证据升级

2026-09-24 联通测试出现关键分层结果：

- 页面 HTTP 200；
- 测试事件 HTTP 201；
- SSE 能收到事件；
- Codex Hook 退出码为 0；
- 但 Hook 消息没有进入协作室。

根因是 `realtime_hook.py` 中实际发送请求的语句缩进错误，导致 HTTP 路径没有执行。修复后增加了直接 `urlopen` 覆盖，并用真实事件计数 `44 → 45` 验证，而不是只相信退出码或 mock。

这一事故形成永久原则：

> HTTP 服务正常、Hook 进程退出正常、消息真正进入时间线、另一 Agent 确认收到，是四种不同证据。

同时确认 Hermes 配置必须依据实际 `HERMES_HOME`，不能假设默认目录；用户级 Hook 还需限制仓库范围，防止把其他项目或私人对话同步进来。

### V2.2：多方名册和现实协作

参与者扩展为 `codex / qoder / github_copilot / workbuddy / codebuddy`。文件邮箱、任务认领和竞赛结果消息已经发生实际使用。

但能力并不对称：只有具备本机可执行入口的 Agent 才可能被独立 worker 唤醒；“消息送达”不能等同于“接管对方已有休眠对话”。

### V3：发现群体趋同，转向反协作塌缩

通信打通后暴露了第二类问题：各 Agent 共享大量信息后，方案开始趋同，整体表现没有随 Agent 数量线性提升，有时甚至无法完成赛题。

系统从“如何让大家都说话”转向“如何让不同路线在必要时不互相影响”。引入三种制度动力：

- **红皇后效应**：真实外部竞争、规则变化和环境变化迫使系统持续校准；禁止伪造危机。
- **鲶鱼效应**：内部挑战者持续攻击共识、寻找结构性替代方案；逻辑常驻，计算上只由事件唤醒。
- **创造性破坏**：让实验市场淘汰旧方案并将资源转向更有证据的新方案，而不是靠多数投票或领导偏好。

### V3.1：双平面与隔离探索

信息被分为两类：

- 必须公开：官方规则、安全告警、基线和工件 SHA、预算、已验证结果、任务 ownership。
- commit 前隔离：候选假设、内部推理、实现方向、参数搜索和竞争性方案细节。

探索岛先独立提交候选哈希、预测和预算承诺，全部 commit 后才能 reveal。提前接触竞争路线的 cell 标记 `contaminated=true`，其结果不再算独立支持证据。

### V3.2：角色权力重构

- Leader 负责目标、预算、期限和执行顺序，但没有宣布技术正确的权力。
- Catfish 每轮拥有一次受保护、预算有限的廉价证伪票，但没有安全绕过权和无限否决权。
- Evaluator 不参与候选设计，按预注册指标匿名评测。
- Integrator 每轮只集成一个主胜者，可保留一个结构差异最大的备选，禁止未经验证的平均融合。
- Safety Guardian 只对合规、安全、不可逆破坏和数据污染硬暂停；技术分歧必须转化为证据挑战。
- Human 保留不可逆操作、真实平台高成本消耗、规则例外和争议升级的批准权。

强制职责分离：同一 Agent 不得在同一轮同时成为候选作者、唯一裁判和最终批准者。

### V3.3：事件唤醒的鲶鱼与外部危机

鲶鱼不是持续运行的“职业反对派”。以下事件才生成挑战请求：

- 有效独立假设不足或候选高度相似；
- 连续两次相同失败且没有新增信息；
- 主路线连续占用过高预算；
- 本地预测与平台结果方向相反；
- Leader 连续否决非共识方案；
- 最终冻结、真实提交或发布之前；
- 外部威胁达到 HIGH/CRITICAL；
- 用户显式发送 `@鲶鱼:` 或 `@challenge:`。

外部危机信号必须记录来源、时间、TTL、可信度、影响范围和最低成本确认方法。`rumor` 只能观察，不能消耗平台额度。

### V3.4：离线 shadow runner

新增 `evolution/evolution.py`，提供：

- `init`：建立一轮影子实验；
- `commit`：验证候选卡，只公开规范化内容的 SHA256、预算和身份；
- `reveal`：所有 cell commit 后验证哈希并揭示；
- `record-result`：绑定候选、工件 SHA、环境、命令、指标和预算；
- `status`：只读查看进度。

安全边界：该工具不调用模型、不轮询、不运行 GPU、不修改候选代码、不自动合并、不提交平台。commit–reveal 提供审计完整性，不等同于操作系统级机密隔离；真正隔离仍依赖独立对话、工作树和访问纪律。

## 3. 当前落地证据（2026-09-28）

### 已验证

- 总协议、shadow runner、候选/结果模板和 T84 回放方案存在。
- Python 编译检查通过。
- 4 项自动测试通过，覆盖：
  - commit ledger 不泄露假设正文；
  - 未全员 commit 时拒绝 reveal；
  - commit 后篡改卡片会被拒绝；
  - 探索预算不能侵占 20% 恢复储备；
  - result 工件 SHA 必须与揭示候选绑定；
  - 结果支出不能超过候选承诺预算。
- 文件邮箱中已存在 Codex、WorkBuddy、Qoder 等真实消息交换和任务认领。

### 本次核验发现的限制

- `http://127.0.0.1:8765/` 当前无法连接，实时协作室此刻离线。
- `competition/coordination/evolution/runs/` 尚不存在，说明没有完成正式 shadow round。
- inbox 仍有未读：Codex 29、Qoder 5、GitHub Copilot 1、CodeBuddy 1；WorkBuddy 为 0。
- GitHub Copilot 已加入名册和邮箱，但目前只有一条发给它的未读消息，尚不能证明完整协作闭环。
- 尚未验证真实的多方 `commit → reveal → evaluator → decision`。
- 尚未验证 WorkBuddy 对新治理协议的明确 ACK，也未验证 Catfish 触发到独立执行的端到端链路。

因此当前准确结论是：

> 各方已经具备协作基础并发生过真实信息交换；新机制已工具化，但尚未证明各方能稳定按照新制度完成闭环。

## 4. 哪些设计有效，哪些仍有风险

### 已证明有效的设计

1. **文件底账 + 实时镜像**：实时服务离线时不会丢失审计链。
2. **显式唤醒而非轮询**：符合 token 节约目标，也降低误启动风险。
3. **严格区分消息状态**：ACK 不等于接受，DONE 不等于 VERIFIED，VERIFIED 不等于 RELEASED。
4. **端到端证据优先**：真实事件增量比退出码、mock 和自报状态更可靠。
5. **SHA 与结果绑定**：减少同名 ZIP 覆盖、错误归因和“成绩属于哪个包”的争议。

### 尚未消除的风险

1. **协议复杂度反噬**：如果协调时间超过总时间 20%，机制会变成更复杂的委员会。
2. **鲶鱼角色固化**：同一 Agent 长期反对可能形成可预测偏见，因此必须轮换模型、Agent 或提示策略。
3. **危机滥用**：外部危机可能被用来制造永久紧急状态和绕过门禁。
4. **伪独立性**：同模型、同上下文生成的多人格不是独立证据；subagent 是临时并行工作单元，不等同于外部 Agent 群。
5. **共享文件不是真隔离**：所有 Agent 可访问同一文件系统时，隔离依赖纪律，不能声称具备强保密性。
6. **Leader 隐性垄断**：即使名义上没有真理权，也可能通过预算分配和排序压制异端，必须保留 Decision Receipt 和问责触发器。

## 5. 失败经验与永久约束

| 失败/误区 | 原因 | 永久约束 |
|---|---|---|
| Hook 退出 0 但没有消息 | 实际 HTTP 路径未执行 | 必须检查真实事件增量和接收端证据 |
| 服务 HTTP/SSE 正常就声称双 Agent 联通 | 只验证了传输层 | 发送、送达、ACK、执行、验证分别取证 |
| 长任务定时查 inbox | 轮询消耗 token 且仍不能唤醒休眠会话 | 新机制只用事件、Hook、按钮或显式暗号 |
| 全员实时共享所有推理 | 方案污染和群体趋同 | 公共事实实时共享，候选推理 commit 前隔离 |
| 多数投票选择技术方案 | 人数不等于独立证据 | 预注册实验和同预算 benchmark 决定胜负 |
| 鲶鱼无限质疑 | 缺少成本和终止条件 | 每次挑战必须可证伪、有预算上限和停止条件 |
| 多方案折中集成 | 机制互相干扰、无法归因 | 每轮一个主胜者，另保留一个结构差异备选 |

## 6. 下一轮迭代路线

### Gate A：T84 历史盲回放

按 `evolution/T84_SHADOW_REPLAY.md` 的时间切片执行：两个常规探索岛、一个鲶鱼岛、一个独立裁判、一个 Leader。平台提交、GPU 和代码修改均为 0。

必须证明：

- 首轮至少 3 个有效独立假设；
- 重复候选率不高于 20%；
- baseline、candidate、result SHA 绑定率 100%；
- 信息增益实验占比至少 80%；
- 连续相同无信息失败不超过 1 次；
- 至少识别一张历史上可避免的平台票，或更早终止一条错误路线；
- 协调成本不超过 20%。

### Gate B：下一道低风险题旁路试点

新机制只旁路记录，不干预正式负责人。每个探索岛最多一个候选、本地判别实验最多两次、平台票默认为 0；余额和授权明确后，每个存活候选最多一张平台票，总上限建议 4 张，并保留至少 20% 恢复额度。

### Gate C：有限接入实时总线

只有 Gate A、B 通过后，才把以下事件接入协作室：

- `ROUND_INITIALIZED`
- `CANDIDATE_COMMITTED`
- `REVEAL_UNLOCKED`
- `CHALLENGE_REQUEST`
- `THREAT_HIGH/CRITICAL`
- `RESULT_RECORDED`
- `HUMAN_APPROVAL_REQUIRED`

这些事件默认只通知，不自动启动模型。只有显式 `execute=true`、严格唤醒暗号或用户按钮才执行。

### 继续 HOLD

- 自动平台提交与覆盖活动 ZIP；
- 自动合并、修改主分支或选择赢家；
- 自动依据相似度拒绝候选；
- 自动调整 Agent 长期信誉；
- 自动解除安全/合规 HOLD；
- 后台持续抓取榜单并唤醒模型；
- 无上限递归 subagent；
- 在预算、授权或平台回执不明时继续执行。

## 7. 版本治理与复盘模板

每次机制升级追加一条记录，不覆盖旧结论：

```yaml
version:
date:
trigger:
verified_evidence:
failed_assumption:
change:
expected_effect:
metrics:
new_risks:
rollback:
status: proposal | shadow | verified | rejected
```

Leader 每轮必须提交 Decision Receipt：

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

协议本身也必须接受创造性破坏：若连续两个试点不能改善首次有效信号时间、独立假设数量、SHA 归因率或最终成绩，应缩减、替换或废止相应环节，不能用“执行不够彻底”无限保护机制本身。

## 8. 工件索引

- 总体通信协议：`competition/coordination/README.md`
- 反协作塌缩协议：`competition/coordination/MULTI_AGENT_ANTI_COLLAPSE_PROTOCOL.md`
- Shadow runner：`competition/coordination/evolution/evolution.py`
- Shadow runner 说明：`competition/coordination/evolution/README.md`
- 候选与结果模板：`competition/coordination/evolution/templates/`
- 自动测试：`competition/coordination/evolution/test_evolution.py`
- T84 盲回放：`competition/coordination/evolution/T84_SHADOW_REPLAY.md`
- 文件邮箱与 ACK/claim：`competition/coordination/bridge.py`
- 实时协作室：`competition/coordination/realtime_hub.py`
- Hook：`competition/coordination/realtime_hook.py`
- Copilot 早期设计文档：`C:\Users\love\WorkBuddy\copilot-worktrees\FlagGems-sglang\love530love-urban-giggle\competition\coordination\MULTI_AGENT_COLLABORATION_EVOLUTION.md`

## 9. 最终复盘判断

这次演化的最大进步不是增加了更多 Agent，而是建立了三条边界：

1. **消息送达不等于协作完成。**
2. **共识数量不等于证据强度。**
3. **制度存在不等于制度已经通过实战。**

真正的目标不是让所有 Agent 更快达成一致，而是让系统在共享事实的同时保留足够多的独立、可证伪路线，并以最少的不可逆成本发现哪条路线更接近事实。

## 10. 2026-09-28 v0.2 Institutional Kernel

公开仓库与本地影子运行器同步加入事件 SHA256 前向链、`audit`、候选/总预算强制校验、结构化 challenge/threat/decision，以及人工批准的 HOLD/resume。公开版本提交为 `bc0e145`；本地同步证据见 `evolution/UPSTREAM_SYNC.md`。两套实现分别执行相同四项测试，均通过。实时 Hub、模型唤醒和真实平台提交仍未接入该内核。
