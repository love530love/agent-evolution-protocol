# 极端无人值守验收矩阵

使用 `resilience_scenarios.run()` 做无外部副作用的故障注入：

- `ack_timeout`：确认 ACK 超时、有界恢复和备用通道决策；
- `restart_recovery`：确认新 supervisor 进程能从磁盘恢复 mission；
- `all_agents_offline`：确认任务仍留在 mission 状态中，输出可观察的接管动作；
- 真实 UI/API 故障仍需在对应 Agent 适配器上做单独验收，不能用模拟测试冒充真实执行。

验收通过不等于“所有外部 Agent 已在线”，而是证明系统不会丢任务、无限重试或把
失败误报为完成。
