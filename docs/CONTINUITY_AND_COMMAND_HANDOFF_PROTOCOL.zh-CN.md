# AEP 连续作战与指挥接力协议

作者：love530love 与 Codex 协作维护  
状态：实施准则；真实原会话投递闭环仍按单独回执验收

## 为什么存在

模型的上下文、积分、订阅额度、单次运行时长、厂商服务和桌面进程都
是会中断的执行资源；它们不是任务本身。AEP 的工作区、原始目标、验收
标准、任务 DAG、证据和工件必须独立于任一个 Agent 存活。任何一名
Agent 在资源将耗尽或已经失联时，都必须留下可由另一名 Agent 接续的
最小事实包，而不是把"继续"留给用户重新解释。

本协议不尝试规避服务商的用量、账户、权限或风控规则。它通过合法的
替代 Agent、已获授权的本地资源和持久化工作区，使一个资源的终止不再
等同于整个任务终止。

## 不可变原则

1. **工作区高于会话。** `workspace_id`、用户原始目标和验收条件是只读
   锚点；会话、模型、客户端和主指挥都可替换。
2. **先交接、后下线。** 一旦预测资源不足、即将切换模型、异常退出或
   有效上下文即将溢出，当前 Agent 必须优先写续战包。
3. **接手必须可核验。** 新指挥只能在核对工作区锚点、快照摘要和未完成
   DAG 后认领；仅看到一段聊天记录不算接管。
4. **派工不等于送达。** `DEPOSITED`、`INPUT_SUBMITTED`、`MESSAGE_VISIBLE`、
   `AGENT_ACK`、`TASK_RESULT_READBACK` 是不同证据等级，禁止跳级陈述。
5. **创造性可竞争，事实不可覆盖。** 候选方案可并行、辩论和红队挑战；
   原始目标、外部副作用记录、原始工件和失败证据只追加、不覆盖。

## 续战包（Continuity Packet）

每个在执行的 Agent 至少在以下时点写包：收到配额预警、主动切换模型、
连续两次工具失败、心跳超时前、准备结束会话前、或用户插话改变优先级时。

最小字段：

```json
{
  "schema": "aep-continuity-packet-v1",
  "workspace_id": "W-...",
  "event_id": "AEP-...",
  "author_agent": "qoder",
  "created_at": "RFC3339 UTC",
  "reason": "TOKEN_EXHAUSTED|QUOTA_EXHAUSTED|CREDIT_WARNING|PROCESS_EXIT|USER_TAKEOVER|TOOL_BLOCKED|MODEL_SWITCH",
  "anchor_ref": "immutable user objective and acceptance digest",
  "snapshot_ref": "verified workspace snapshot digest",
  "dag": {"completed": [], "running": [], "next": [], "blocked": []},
  "artifacts": [{"path": "relative/path", "sha256": "...", "meaning": "..."}],
  "observations": ["only evidence-backed facts"],
  "failed_attempts": [{"action": "...", "result": "...", "retry_safe": false}],
  "next_actions": [{"task": "...", "acceptance": "...", "suggested_capability": "..."}],
  "required_reply": "AEP-TAKEOVER-ACK <event_id> <snapshot_digest>"
}
```

`reason` 表示观察到的中断类型，不把未知情况伪装成额度耗尽。敏感凭据、
Cookie、令牌和用户私人聊天内容不得写入包；只引用受控位置和摘要。

## 指挥权接力状态机

```text
RUNNING
  -> WARNING_DETECTED       配额/上下文/心跳/进程信号
  -> PACKET_SEALED          续战包 + 快照摘要写入
  -> RELIEF_CANDIDATE       匹配能力、可用性、会话路径
  -> TAKEOVER_ACKED         新指挥核验锚点并认领
  -> REASSIGNED             新指挥重派未完成 DAG 节点
  -> RESULT_READBACK        原会话或工件证据回读
  -> COMPLETED | HOLD
```

任何环节缺少可验证证据都进入 `HOLD`，并保留旧指挥的工件为只读档案；
不得为了保持表面活跃而反复创建新会话或重复发送同一外部动作。

## 调度策略

### 资源降级与替代

| 故障类别 | 当前 Agent 必做 | 调度器动作 | 禁止的假结论 |
|---|---|---|---|
| Token/上下文临界 | 写续战包、指出下一最小动作 | 将同一快照交给能力相同的候选者 | “新会话已理解全部上下文” |
| 厂商积分/额度耗尽 | 写可重试时间与已完成工件 | 选择已获授权且健康的替代 Agent | “已绕过额度” |
| 进程退出/无心跳 | 使用最近封存包 | 标记旧实例只读、派给候选者 | “进程重启即任务恢复” |
| 桌面权限/UIPI 阻断 | 写窗口、会话、完整性和阻断证据 | 走已注册提升执行器或改用可读 transport | “已发送” |
| 用户临时接管 | 立即封包、传播最高优先级用户事件 | 暂停冲突订单，重新规划 | “旧计划仍自动继续” |
| 多方案冲突 | 交给独立评审/红队 | 保存分歧和证据，选可验证实验 | “多数投票即事实” |

### 反平庸机制

每个长期工作区保留三种并行角色：主执行者、独立证据审查者、挑战者。
挑战者只提出可检验替代方案；主执行者必须把未采用原因和最小验证成本
写入续战包。这样“红皇后/鲶鱼/创造性破坏”产生的是可复核实验，而不是
无休止改写目标或制造无效任务。

## 实施优先级与验收

1. **P0：真实投递。** 每个桌面客户端完成
   `SESSION_LOCATED -> INPUT_SUBMITTED -> MESSAGE_VISIBLE -> AGENT_ACK -> TASK_RESULT_READBACK`。
2. **P0：连续性写入。** 为派工订单增加 `CONTINUITY_REQUIRED` 检查；预警、
   超时或用户接管必须能产生并校验续战包。
3. **P1：健康注册表。** 记录 Agent 的能力、当前会话锚点、心跳、已知
   限额状态（仅自主报告）、可用替代者与最近接力包；不采集或伪造厂商凭据。
4. **P1：接管演练。** 人为注入 `TOKEN_EXHAUSTED`、`PROCESS_EXIT`、
   `UIPI_BLOCKED`，验证旧指挥停写、新指挥从快照接力且任务图不重复。
5. **P2：无人值守。** 常驻协调器只处理已验证订单和到期心跳；它必须
   生成 `HOLD` 与升级材料，不能把无回执的循环重试当作进展。

一个工作区只有在 P0 的原会话证据链与 P1 的一次接力演练都通过后，才可
宣称具备连续作战能力。其余状态一律标记为“可用组件”或“待实机验收”。

## 每个接入 Agent 的最短提示词

> 你是 AEP 联邦工作区中的可替换执行者。工作区高于本会话。开始前读取
> 锚点、快照和未完成 DAG；结束、额度预警、模型切换、工具失败或用户接管
> 前，先写 `aep-continuity-packet-v1`。只陈述已核验证据；没有
> `MESSAGE_VISIBLE`、`AEP-ACK` 和结果回读时，不得声称任务已被对方接收或完成。
