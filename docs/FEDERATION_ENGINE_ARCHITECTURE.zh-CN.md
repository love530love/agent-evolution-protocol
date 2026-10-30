# 持久联邦 Agent 工作区引擎：架构与实现（2026-10-30）

> 状态：**原型已可运行、有测试**；真实 A2A/MCP 传输、浏览器底座对接、跨机联邦**未验收**。
> 本文只写能复算的东西：模块位置、schema、测试命令、失败边界。

## 0. 一句话架构

工作区是任务的唯一持有者，Agent 是可替换执行者；调度内核只通过适配器说话，
所有状态落在磁盘上并带水印与哈希链，接管只传结构化快照、不传对话全文。

```
                    ┌──────────────────────────────────────────┐
   用户一次任务 ───▶ │  Workspace (W-xxxx)  ← 唯一任务持有者     │
                    │  anchored_request │ dag │ artifacts         │
                    └───────────────┬──────────────────────────┘
                                    │ 读/写 state.json + events.jsonl
                    ┌───────────────▼──────────────────────────┐
                    │  Scheduler Kernel (engine.py)            │
                    │  tick: 心跳→排程→投递→接管→记账           │
                    └───────┬───────────────────────┬──────────┘
                            │ 诊断                    │ 接管包
                    ┌───────▼────────┐      ┌────────▼─────────┐
                    │ Diagnosis      │      │ Handoff Packet   │
                    │ 层/动作/预算    │      │ 指向 snapshot     │
                    └────────────────┘      └──────────────────┘
                            │
                    ┌───────▼──────────────────────────────────┐
                    │ Adapter (capabilities/heartbeat/deliver)  │
                    └───────┬───────────┬───────────┬──────────┘
                         A2A 适配器   MCP 适配器   桌面/UIA 适配器
```

## 1. 公理 → 实现位置（逐条可查）

| 公理 | 实现 |
| --- | --- |
| 工作区持有任务，Agent 只是执行者 | `engine.create/load/save`：状态只存在 `workspaces/<WID>/state.json`；Agent 归档在 `state.agents` |
| A2A + MCP 互通 | `adapter.py` 只定义四个调用；A2A/MCP 细节留在适配器内部，内核不 import 任何厂商 SDK |
| 分层解耦 | 底座=适配器；内核=`engine.py`；业务=Agent；常驻=`diagnosis.py`、`evolution.py` |
| 接力传快照不传全文 | `snapshot.py` 三区结构 + `handoff.py` 只引用 `snapshot_digest` |
| 全链路水印 | `workspace_identity.watermark` + `store.EventLog` 每条事件带 `workspace_id`，不符即隔离 |

## 2. 模块划分

| 文件 | 职责 | 关键约束 |
| --- | --- | --- |
| `federation/store.py` | 原子写、事件链 | `os.replace` 原子落盘；`seq` 连续、`prev_sha256` 链接、内容哈希；篡改可检出 |
| `federation/snapshot.py` | 三区快照 | anchor 只读（改动即 `ANCHOR_MUTATED`）；快照自哈希；版本可回溯 |
| `federation/handoff.py` | 接管包 | 只引用 `snapshot_digest`；目标 Agent 必须自称并持能力；重定向即 `HANDOFF_DIGEST_MISMATCH` |
| `federation/diagnosis.py` | 故障分类 | 10 类信号 → (层, 类别, 动作)；未知即 escalate；retry 有预算 |
| `federation/evolution.py` | 策略库 | proposed/promoted/retired；晋升是证据的纯函数；只追加历史 |
| `federation/adapter.py` | 适配器边界 | 四个方法；`FakeAdapter` 供测试；能力匹配确定序 |
| `federation/engine.py` | 调度内核 | tick 五步；接管必须真正投递；旧 Agent 归档不删除 |

## 3. 标准 Schema（复用，不另立）

引擎**不新增 schema 文件**。它写出的每份文档都校验通过工作区已有的标准：

| 引擎产物 | 遵循的既有 schema | 字段落点 |
| --- | --- | --- |
| 工作区快照 | `schemas/workspace-snapshot-v1.schema.json` | `anchored_request.text/acceptance/constraints`、`dag.nodes[]`、`intelligence`、`artifacts`；`at`/`digest`/`parent_snapshot` 走 `additionalProperties` |
| 审计事件 | `schemas/event-envelope-v1.schema.json` | `event_id`、`kind`、`actor`、`parent_event_id`、`payload`；`seq`/`prev_sha256`/`sha256` 为扩展字段 |
| 接力包 | `schemas/handoff-packet-v1.schema.json` | `event_id`=`handoff_id`、`snapshot_ref`=`snapshots/vNNNN.json#<digest>`、`next_action`、`evidence`；`reason`/`snapshot_digest`/`anchor_sha256` 为扩展字段 |
| 适配器清单 | `schemas/agent-capability-bundle-v1.schema.json` | `adapter.bundle()` 把任意适配器渲染成能力包 |

为什么没有单独的「DAG 任务 schema」：任务节点是 `workspace-snapshot-v1` 里 `dag.nodes`
的元素，节点形状由 `snapshot._nodes()` 固定（`id/requirement/deps/state/owner/attempts/signal/note`），
而 `task-envelope-v2` 描述的是**派单时刻**的信封，生命周期阶段不同，不能混用。
硬把节点塞进 `task-envelope-v2` 就得为每个节点编造一个 `acceptance`，那是伪造证据。

契约由 `tests/test_federation_conformance.py` 用 `jsonschema` 实际校验，不是只查 key 存在；
另有 `NoPrivateSchemaTests` 守住「不许再长出第二套标准」这条线。

## 4. 适配器接口规范（唯一允许的跨界方式）

```python
class Adapter(Protocol):
    agent: str
    def capabilities(self) -> set[str]: ...          # 能被派什么活
    def heartbeat(self, now) -> dict: ...            # {agent, alive, last_progress_at, now}
    def deliver(self, task) -> dict: ...             # {status: accepted|refused|failed, signal, note}
    def observe(self) -> dict: ...                   # 当前状态与最近输出摘要
```

规则：
1. 适配器**只报事实**，不做判断、不改 anchor、不替内核决定重试。
2. `deliver` 返回 `accepted` 只代表"接单"，不代表完成；完成走 `engine.complete` 并带工件摘要。
3. 失败必须给 `signal`；给不出就返回 `failed` 且留空，内核记为 `unclassified` 并上报，不自动重试。
4. 一个厂商一个适配器；厂商鉴权、会话复用、CDP/UIA 细节都在适配器内，不泄漏进内核。
5. `from_agent != to_agent`、`reason` 枚举、`snapshot_ref` 必须钉住声明的 digest —— 这三条由
   `handoff.verify()` 运行期校验，JSON Schema 表达不了跨字段语义。

`adapter.bundle(adapter, *, transport, kind)` 把适配器渲染成 `agent-capability-bundle-v1`
文档，新人拿到的是一份可被既有工具读取的清单，而不是一段散文。
`kind`/`transport` 的取值在 `adapter.CONNECTOR_KINDS` / `TRANSPORTS` 里与 schema 枚举同步，
非法值在生成阶段就抛错，不会产出一份下游才失败的文档。

## 5. 六项能力 → 现状

| 要求 | 现状 | 证据 |
| --- | --- | --- |
| 1. 持久化与跨重启恢复 | 已实现 | `test_state_survives_a_restart`、`test_tampered_event_chain_blocks_recovery` |
| 2. 异构 Agent 唤醒/派工/加入 | 接口已定，真实传输未接 | `test_ready_task_is_delivered_to_a_capable_agent`；A2A/MCP 适配器**未实现** |
| 3. 无损接力（token 耗尽/卡死/换模型） | 已实现（快照+接管包+归档） | `test_token_exhaustion_hands_the_task_to_a_successor`、`test_stalled_agent_is_replaced` |
| 4. 阻断识别与自愈 | 已实现分类与有界动作 | `test_every_known_signal_maps_to_layer_and_action`、`test_no_successor_blocks_instead_of_losing_the_task` |
| 5. 自主进化闭环 | 已实现策略状态机与继承 | `test_promotion_after_threshold`、`test_only_promoted_strategies_are_inherited` |
| 6. 全链路审计 | 已实现哈希链与快照版本 | `test_edited_event_is_detected`、`test_rollback_returns_verified_old_version`、`test_every_event_line_validates_against_the_published_schema` |

## 6. 极端场景覆盖（测试即验收）

| 场景 | 测试 |
| --- | --- |
| 断电/重启后恢复 | `test_state_survives_a_restart` |
| 事件被篡改/删除/跨工作区注入 | `test_edited_event_is_detected`、`test_deleted_event_is_detected`、`test_foreign_workspace_event_is_detected` |
| Token 耗尽接管 | `test_token_exhaustion_hands_the_task_to_a_successor` |
| Agent 卡死（心跳超时） | `test_stalled_agent_is_replaced` |
| Agent 拒绝执行 | `test_refusal_switches_agent_without_retrying_forever` |
| 无可用继任者 | `test_no_successor_blocks_instead_of_losing_the_task` |
| 依赖失败传导 | `test_failed_dependency_blocks_downstream` |
| 外部消息污染 | `test_foreign_message_is_quarantined` |
| 结论冲突 | `test_conflict_opens_an_arbitration_task` |
| 人工暂停/恢复 | `test_paused_workspace_does_nothing` |
| 接管包被重定向 | `test_retargeted_packet_is_detected` |
| 快照被改 | `test_tampered_dag_breaks_the_digest`、`test_edited_anchor_inside_the_snapshot_is_refused` |
| 回滚到历史版本 | `test_rollback_returns_verified_old_version` |
| 归档 Agent 被重复派活 | `test_archived_agent_is_not_offered_new_work` |
| 产物偏离既有标准 | `test_a_full_handoff_run_leaves_only_conformant_documents`、`test_every_schema_file_is_versioned` |

复算：`PYTHONPATH=src D:/A/python.exe -m pytest tests/test_federation_*.py -q`
端到端时间线：`PYTHONPATH=src D:/A/python.exe examples/federation_demo.py`

## 7. 一次真实的自我纠错（2026-10-30）

第一版引擎自带四份 `schemas/federation-*.schema.json`。全量回归立刻挂了：

```
FAILED tests/test_federation_schemas.py::test_federation_schema_assets_are_valid_json_and_have_ids
1 failed, 638 passed
```

原因不是测试太严，是**我另立了一套标准**：工作区早已有 `workspace-snapshot-v1`、
`event-envelope-v1`、`handoff-packet-v1`、`task-envelope-v2`，我却又造了同名的第二套。
两份标准并存比没有标准更糟——接手的 Agent 不知道该信哪个。

处置：删掉那四份文件，把引擎输出改成符合既有标准，并用 `jsonschema` 真校验。
没有去改别人的验收测试来让自己通过。留 `NoPrivateSchemaTests` 守住这条线。

## 8. 明确未做（NOT_VERIFIED）

1. **真实 A2A / MCP 适配器**：目前只有 `FakeAdapter`；跨进程、跨机器的联邦没有实测。
2. **浏览器底座对接**：`capabilities` 里可以声明 `browser`，但没有实现真正调用底座的适配器。
3. **多机**：事件链与快照假设单机文件系统；多机需要共享存储与锁，未设计。
4. **真实 Token 计量**：`token_exhausted` 是适配器上报的信号，内核不自己数 token。
5. **并发写**：同一工作区多进程同时 tick 未加锁，当前假设单调度器。

## 9. 下一步优先级

1. 写一个真实 A2A 适配器（本地回环即可），验证 `deliver/observe` 不是纸面契约；
2. 给 `state.json` 加文件锁，允许两个调度器并存而不互相覆盖；
3. 把 `evolution.py` 的策略真正接回 `engine.tick`（现在策略库与调度是两条线，尚未联动）；
4. 用真实任务跑一次"跨重启 + 换 Agent"的端到端，产出可对账的时间线。
