# 唤醒与交付真值表

| 阶段 | 含义 | 是否可称为唤醒成功 |
|---|---|---|
| `DEPOSITED` | 请求写入协作区/队列 | 否 |
| `WINDOW_FOUND` | 找到候选窗口 | 否 |
| `INPUT_SUBMITTED` | 输入事件已发出 | 否 |
| `MESSAGE_VISIBLE` | 原会话中能读到消息 | 否，仅表示消息可见 |
| `AGENT_ACK` | 目标 Agent 明确确认并带事件证据 | 是，表示接单 |
| `WORKING` | 目标 Agent 已开始执行 | 是，表示行动中 |
| `EVIDENCE_RETURNED` | 返回结果、日志或截图 | 是，表示有产出 |
| `VERIFIED` | 结果经回读和验收 | 是，表示任务完成 |

## 强制规则

- 入队不能冒充投递；
- 窗口锚定不能冒充会话接入；
- 输入 API 返回不能冒充消息可见；
- 消息可见不能冒充 Agent 接单；
- Agent ACK 不能冒充任务完成；
- 只有 `VERIFIED` 才能关闭任务。

Computer-use、UIA、CDP 或 MCP helper 超时后，状态必须保持原状态并记录 `TRANSPORT_UNAVAILABLE`，不能自动重放可能重复的外部动作。
