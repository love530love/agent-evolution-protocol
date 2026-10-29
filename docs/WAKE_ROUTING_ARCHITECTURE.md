# 多通道唤醒路由架构

ClawChat 只是 transport，不是调度真相源。所有 Agent 先进入 session registry，
唤醒请求必须解析到唯一的 `agent + workspace + session/conversation/window`。

## 路由顺序

1. 原生 MCP/API
2. ClawChat machine channel
3. 本地 stdin/stdout runner
4. WebSocket/HTTP relay
5. Windows UI Automation
6. 浏览器扩展/CDP
7. 可视 GUI 回退

每个 transport 只返回结构化结果。`DELIVERED` 仅表示消息已送达；只有目标 Agent
明确返回带同一 `event_id` 的 ACK，才进入 `ACKNOWLEDGED`。无唯一会话返回
`NO_SESSION` 或 `AMBIGUOUS_SESSION`，禁止猜测、盲点、默认新建会话或隐式重试。

每次发送前建立一个带 `event_id` 的 ACK lease。等待只针对这个已存在的事件，且有
明确超时；超时写成 `ack_timeout`，不会转化成无限后台轮询。超时任务可以进入备用
transport、人工仲裁或 dead-letter 队列。

恢复策略也按状态分级：传输故障可有限次数切换 transport；会话歧义、权限和认证
错误直接阻断；高风险副作用在 ACK 超时后进入人工仲裁；超过上限的任务写入
dead-letter，不允许无限重试。

默认调用是 preview；生产执行必须显式 `execute=True`。失败应切换下一个已注册
transport，但不能重复发送同一高风险副作用任务；由上层 operation key 去重。
