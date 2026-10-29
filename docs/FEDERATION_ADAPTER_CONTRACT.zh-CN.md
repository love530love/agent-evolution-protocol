# 联邦 Agent 适配器契约

适配器把厂商会话或本机执行器映射到统一联邦内核。适配器不能自行改变 workspace 目标，
也不能把“窗口聚焦”冒充为 Agent ACK。

## 必须提供的操作

```text
discover(workspace_id) -> candidates[]
register(workspace_id, agent_id, session_id, conversation_id, capabilities)
wake(event_envelope) -> wake_receipt
deliver(event_envelope, session_binding) -> delivery_receipt
readback(event_id, session_binding) -> readback_receipt
heartbeat(workspace_id, agent_id, session_id) -> heartbeat_receipt
archive(session_binding) -> archive_receipt
```

## 统一状态

`DISCOVERED → REGISTERED → WAKE_QUEUED → WINDOW_FOUND → INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK → EVIDENCE_RETURNED → VERIFIED`

失败必须带 `failure_class`（agent、transport、resource、external-interference）、`retry_after`、
`evidence` 和 `next_action`。未知状态进入 `UNKNOWN`，不能自动升级为成功。

## 会话绑定

适配器必须同时提供 `workspace_id`、稳定 `agent_id`、`session_id`，以及可选的
`conversation_id/window_id`。多候选或会话漂移时返回 `AMBIGUOUS_SESSION`，停止输入，等待重新定位。

## 幂等与恢复

- 每次操作使用 `event_id` 幂等；重复事件返回已有回执；
- 默认复用原会话，未经工作区策略显式授权不得新建；
- 进程重启后从快照恢复，不从模型聊天全文猜测状态；
- 适配器不可用时，消息仍必须落入 durable mailbox，供低能力 Agent 接力。
