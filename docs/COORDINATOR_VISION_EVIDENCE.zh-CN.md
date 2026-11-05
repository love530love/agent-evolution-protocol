# 居中协调长任务 Agent：事实基线、断言证据与分期路线

记录日期：2026-11-05（v0.6.20 基线）

## 0. 这份文档是什么、不是什么

它**不是**设计完成稿，**不是**「已实现」声明，也**不是**路线图已经获批。
它把「一个代替人类工程师、居中调动所有 Agent 的长任务协调器」这个愿景，
拆成一条条**可复算的断言**，逐条标注当前证据状态：站得住 / 有代码无消费者 /
完全没有数据 / 只在文档里。

之所以先写这份，是因为操作员本人提出了约束：
「有些观点需要实据，避免团队受错误情报影响而越来越差劲」。
在一个以证据链为核心的项目里，最不能被接受的事，就是协调器自己先靠未经核验的断言立项。

---

## 1. 愿景拆成 8 条断言

| # | 断言 | 当前证据状态 |
|---|---|---|
| A1 | 自动化 / 定时 / 按估算时长主动轮询与插话 | **部分站得住**：轮询与插话有实测代码；「按估算时长」无实现 |
| A2 | 演化成独立、居中协调调度的长任务 Agent 项目 | **无**：CLI 无 mission 子命令，无 runner，无自启动器 |
| A3 | 引入开源项目能力，让手和脚更健壮 | **可行但受硬约束**：见第 4 节，只宜引协议/字段语义，不宜引运行时 |
| A4 | 任何多 Agent 长程任务由它居中完成分工、传递、唤醒、回表 | **部分站得住**：分工/唤醒/回表各有实现，但彼此未接线、无统一一拍 |
| A5 | 相当于代替人类工程师 | **不成立**：见 3.3 |
| A6 | 调动所有存活或可启动的 Agent 协助执行 | **部分站得住**：`client_registry` / `presence` / 桌面唤醒可用；启动 Agent 不等于接单 |
| A7 | 把各 Agent 额度内能力极致调动，从而省积分 | **没有数据支撑**：见 3.1 |
| A8 | 避免偏听偏信，观点需实据 | **字段级有、核验级没有**：见 3.2 |

---

## 2. 三态能力矩阵（本次只读盘点实据）

### 2.1 已实测跑通（有代码 + 有消费者 + 有测试）

| 能力 | 位置 |
|---|---|
| 订单状态机 `submit → dispatch → ack → tick → watch` | `coordination/commander.py:129/824/1056/1249/1336`，`aep coord-commander`，`scripts/aep-supervisor.ps1` |
| DAG 心跳 / stalled / handoff / recover / serve | `federation/engine.py:220/326/748`，`federation/supervisor.py:78/461` |
| 认领冲突、租约、在线信号、客户端登记 | `claim_conflicts.py:257/318/391`、`presence.py`、`client_registry.py`、`coord-claim-renew` |
| 会话身份、投递见证、轨迹复算 | `session_fingerprint.py`、`action_verification.py`、`delivery_audit.py`、`trajectory.py` |
| 回表 / ACK 截止 / 催促 / 全队通报（分散） | `campaign_cycle.py:155/260/297/350`、`hold_alerts.py:71`、`command_memory.py:264 announce()` |
| 事件驱动唤醒循环 | `scripts/campaign-watch.ps1`（FileSystemWatcher + `campaign_tick` + `wake_daemon --once`） |
| 桌面主动唤醒与 Qoder 插话 | v0.6.16 实测，`skills/aep-desktop-wake/SKILL.md` |
| 一次性 mission 接合（入队 `DEPOSITED`） | `coordination/mission_pulse.py`，v0.6.19 |

### 2.2 有代码、有单测，但无消费者

`mission_planner.py`、`mission_runtime.py`、`mission_graph.py`、`mission_supervisor.inspect()`
（只返回动作建议不执行）、`artifact_manifest.verify()`、`resilience_scenarios.py`、
`recovery_policy.decide()`、`insight_miner.py`、`joint_attack.py`（五角色对抗 + 哈希链）、
`quorum.py:77 evaluate()`（N-of-M + `action_sha256` + `evidence_sha256` + 禁自批）、
`legion_ledger.py`（贡献评分/反躺平）、`volley_plan.py:48`、`transcript_bus.py`（只记录不推送）、
`command_memory.announce()` 虽存在但注释明写 "Mailbox delivery is not read or ACK"。

`engine.complete()` 收 `artifact_path` 却不调 `artifact_manifest.verify()`——这是最典型的一处「验收入口就在旁边却没接」。

### 2.3 完全没有实现

- **per-Agent 积分额度 / token 用量 / rate limit / 重置间隔 / cost 记账 / 额度自动调配**；
- 统一 `broadcast` / `notify_all` API，以及「通报全队 → 催促查看回表」的完整接线；
- 消息来源可信度自动核验、**「共同来源只算一个来源」的自动判定**、claim↔evidence 自动比对；
- 持久化调度游标（`commander.tick` 每次全量重扫 inbox）、跨重启 **mission** 恢复（仅 federation 有 `engine.recover`）、
  artifact 验收接线、通知定级/升级策略；
- mission runner / CLI `mission*` 子命令 / 仓库自启动器。

### 2.4 只在文档里

`docs/SUPERVISOR_24X7.zh-CN.md`、`docs/UNATTENDED_LONGRUN_AUDIT.md`（自认「不能宣称完全闭环」）、
`docs/COLLABORATIVE_REASONING_FRAMEWORK.zh-CN.md`（第 3 行自述「不能据此推断全部运行功能已经实现」）、
`docs/EXECUTOR_DELEGATION_PLAYBOOK.zh-CN.md`、`docs/NOVICE_LONGRUN_WORKFLOW.zh-CN.md`。
「每 15 分钟续租」也只是 `federation/assignment.py:95` 渲染进派工文本的一句话，不是调度器。

---

## 3. 三个必须先证伪的断言

### 3.1 「省积分」目前算不出来

`legion_ledger.py` 是**贡献评分**账本，注释明写 "not transferable credits"、"grants no operational authority"；
`cli.py` 的 `init_round/commit/record_result` 是**单轮赛制预算**（`total_budget` / `budget_reserved` / `budget_spent`）；
`volley_plan.plan(quota_remaining, reserve, wave_size)` 带 `PROPOSAL_ONLY_NOT_AUTHORIZED`；
`reasoning_contract.py` 的 `budget` 注释是 "planning data"。
`CHANGELOG.md` 自己写着 "no multi-day budget/quota scheduling, no notification policy"。

要谈「极致调动额度」，至少要有四类事实：每个 Agent 的额度、已用量、重置时刻、单次任务成本估计。
**四类数据一个都没有** ⇒ 现在任何「节省 X%」都是猜测。第一步不是优化，是**记账**。

### 3.2 「避免偏听偏信」目前靠字段，不靠核验

`cli.py:81-85` 定义了 `THREAT_FIELDS`（`source / observed_at / freshness_ttl / confidence /
cheapest_confirmation / response_deadline`）与 `DECISION_FIELDS`，但 `governance_event():499`
只校验**字段齐全**与 `confidence ∈ {verified, inferred, rumor}`，不核验内容。
`quorum.evaluate()` 与 `joint_attack` 的交叉验证机制都在，但**没接进派工路径**。
`docs/COLLABORATIVE_REASONING_FRAMEWORK.zh-CN.md:94` 自己写了「五个模型引用同一篇错误资料，
仍只有一个错误来源」，却没有对应的自动判定。

**本次外部检索就抓到一个现成反例**：某中文技术博客把 MCP 全称写成
"Multi-agent Coordination Protocol"，并附会「任务拍卖 / 区块链贡献记录 / PBFT 共识」；
而官方 `modelcontextprotocol.io`（2026-07-28 版）的定义是
**Model Context Protocol**，一个把 AI 应用连到外部系统（数据源、工具、工作流）的开放标准，
类比为「AI 应用的 USB-C」。若把前者当作事实喂给团队，多个 Agent 转述一百次也不会变成两个独立证据。
这正是「来源数量 ≠ 独立证据数」的实据样本，也说明 P2 的核验不能靠模型自觉。

### 3.3 「代替人类工程师」现阶段不成立

v0.6.19 边界已写死：没有注册任何 OS 计划任务、没有常驻进程、没有跨重启恢复演练、
不承诺 7x24 无人值守；OS 计划任务本身还需要登录会话。
本机由 agent 内部 spawn 的常驻进程会随工具调用结束被**作业对象回收**，
`CREATE_BREAKAWAY_FROM_JOB` 不被允许，常驻只能由 agent 之外的东西建立。
关单仍需 `VERIFIED`，`improvement` 链要求 reviewer≠author 且 `PROPOSED` 不自动晋升。

准确说法是：**有证据的协调副驾**，不是替代者。在这条被推翻之前写「代替人类工程师」，
就是项目自己最该防的那种错误情报。

---

## 4. 开源能力准入：引协议，不引运行时

| 项目 | 官方事实（2026-11-05 取回） | 能补什么 | 为何不宜直接引运行时 |
|---|---|---|---|
| **MCP** | `modelcontextprotocol.io`：Model Context Protocol，AI 应用 ↔ 外部系统的开放标准（工具/资源/提示词），规范版本 2026-07-28 | 把手脚（唤醒、读表、写回表）标准化成外部可发现的工具 | 项目已有 UIA/OCR 适配器与 CLI；MCP **不管多 Agent 协调**，为协议重写手脚等于第二套实现 |
| **A2A** | `a2a-protocol.org`：Agent2Agent，Google 捐给 Linux Foundation，Apache-2.0，agent ↔ agent 发现/委派/交换结果；官方明确「不是 agent 开发套件、不负责子 agent 或工具调用、不是消息应用」 | 跨框架 Agent Card 与委派消息格式 | 项目已有 `dispatch-envelope-v1` / `task-envelope-v2` / `handoff-packet-v1` 三套 schema；**再加第四套就是并存标准**。A2A 也不提供 durable orchestration / quota / evidence verification |
| **Temporal** | `docs.temporal.io/self-hosted-guide`：自托管需 Temporal Service（Docker/K8s/手动）+ 持久化存储 + Worker；本地可用 `temporal server start-dev` 单二进制 | 持久化游标、跨重启恢复、事件历史（正是 2.3 的缺口） | 仍是**常驻服务 + DB**，与「不卡顿、不常驻、OS 触发归操作员」直接冲突。可借鉴其 event-history 语义，不宜部署 |
| **LiteLLM Proxy** | `docs.litellm.ai/docs/proxy/users`：`max_budget` / `budget_duration` / `budget_limits` / `model_max_budget` / `tpm_limit` / `rpm_limit` / `max_parallel_requests` / `spend` / `budget_reset_at`；窗口规则明确（24h 每日 UTC 零点、7d 每周一、30d 每月 1 日） | per-Agent 额度账本的**字段语义与窗口规则** | 官方明写 "Budgets require a database"、"none of them cap anything on a DB-less deployment"，且需运行中的 Proxy 与默认每 10 分钟的 reset scheduler ⇒ 只借规范，在项目内做成纯文件账本 |

**准入四条硬约束**（任何开源引入都要过）：

1. 库级、可嵌入、**无独立服务 / 无数据库 / 无后台线程**；
2. 不新增 schema、不新增 `coord-*` 命令面（除非走 `aep coord-skill-manifest write` 并守线）；
3. 判断仍是规则、可复算、fail-closed；模型只做 `advisory`，无否决权；
4. 单次调用有界、默认 dry-run、进程退出即结束；OS 触发归操作员。

---

## 5. 分期路线（每期都必须带可复算验收）

| 期 | 内容 | 完成判据 |
|---|---|---|
| **P0 一拍接线** | 一次性 `coordinator_tick`：按固定顺序调用既有单步（mission 扫描 → campaign 回表/截止 → `commander.tick` → `supervisor.step` → 催促/通报），默认 dry-run，`--apply` 才写，统一回执 | 不新建调度器；既有单步签名不变；真协作区只读预览零写入；沙盒 `--apply` 幂等 |
| **P1 额度可计算** | per-Agent quota ledger：额度声明、窗口与重置时刻、用量记录、派工前闸门。先回答「谁还有额度、何时重置」，不谈优化 | 能输出每个 Agent 的 `remaining / reset_at`；无数据时明确报 `NO_QUOTA_DATA` 而不是猜测 |
| **P2 实据可核验** | claim↔evidence 绑定、共同来源去重、`quorum.evaluate` 接进派工路径 | 同一 `source` 的 N 个转述计为 1 个独立来源；无证据观点不得进入 `verified` |
| **P3 手脚标准化** | 唤醒 / 插话 / 读表 / 回表做成可选 MCP tools 或 A2A Agent Card 描述，**仅作对外接口**，内部仍走既有适配器 | 不改动既有桌面适配器；不新增 envelope schema |
| **P4 闭环** | `artifact_manifest.verify` 接线、通知定级、跨重启 mission 恢复、持久化调度游标 | 关单仍需 `VERIFIED`；恢复演练有前后状态与哈希证据 |

每期固定流程：先写验收测试 → 全量回归（本机须 `CODEBUDDY_SAFE_DELETE_ENABLED=0`）→
真协作区只读预览 → 临时沙盒 `--apply` 幂等 → 按「每次更新务必 push 及发版」交付。

---

## 6. 明确不做（写在这里防止被别处误读成已完成）

- 不宣称「代替人类工程师」「7x24 完全无人值守」「已节省积分」；
- 不启动常驻 watcher / 后台线程 / model server / Postgres / Proxy；
- 不由 agent 自己注册 OS 计划任务；
- 不用概率模型做心跳、闸门或裁判；
- 不把 `DEPOSITED` 写成送达，不用 `VERIFIED` 之外的状态关单；
- 不加第四套 envelope schema，不新建第二套调度器。

---

## 7. 本轮实据来源

| 来源 | 用途 |
|---|---|
| 仓库只读盘点（2026-11-05）：`coordination/` 125 个模块、`federation/`、`cli.py`、`scripts/`、`tests/` 160 个文件 | 第 2 节三态矩阵、第 3.1 / 3.2 节代码事实 |
| `https://modelcontextprotocol.io/`（规范 2026-07-28） | MCP 官方定义，用来证伪博客误称 |
| `https://a2a-protocol.org/latest/` | A2A 治理、范围、明确「不是什么」 |
| `https://docs.temporal.io/self-hosted-guide` | 自托管运行足迹 |
| `https://docs.litellm.ai/docs/proxy/users` | 预算字段、窗口规则、"Budgets require a database" |
| 本轮 WebSearch 结果中的中文博客（MCP 误称、PBFT 附会） | 3.2 节「来源数量 ≠ 独立证据数」反例；**仅作为反例引用，不作为事实来源** |
| `docs/releases/v0.6.19.md`、`docs/LONGRUN_MISSION_PULSE.zh-CN.md`、`CHANGELOG.md` | 3.3 节既有边界 |
