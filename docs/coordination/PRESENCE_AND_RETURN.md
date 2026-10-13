# 队友归队与战力变化通报

协作区现在区分四种状态：`online` 是近期收到本人会话 hook 或明确自报，`offline` 是明确退出，`stale` 是在线证据到期但**不能证明已下线**，`unknown` 是从未收到可靠信号。应用进程仍在、消息已投递、窗口被唤起，都不自动算在线或已接单；任务仍需单独 ACK。

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m agent_evolution_protocol.coordination.presence status --root coordination
python -m agent_evolution_protocol.coordination.presence signal --root coordination --agent qoder --state online --source agent-self-report --session-id original-session
python -m agent_evolution_protocol.coordination.presence signal --root coordination --agent qoder --state offline --source agent-self-report --reason leaving
```

Codex/WorkBuddy 现有会话 hook 的 `start/end` 自动发信号；`stop` 是完成一轮回复，会续期在线证据，**不会误报离线**。其他客户端接入后可调用同一 `signal` 命令，或由其可信适配器上报。在线信号默认 30 分钟过期，可在 1–120 分钟内按客户端特性调整；无续报时，在到期点转为 `stale`。这只是通信证据的期限，不要求所有 Agent 为保持“绿灯”而持续消耗模型 token。

每次真正的状态变化只生成一次 `coordination/presence/notices/*.json`，同时作为状态事件写入本地共享会话日志；重复在线续期不刷屏。归队时，根据已有已读游标生成 `coordination/presence/digests/*.json`，含未读数量和最近记录，**不会替 Agent 自动标已读**。离队或失联通报包含仍活跃的任务，建议核查、必要时唤醒或重分工；不会因一个失联信号自动重复派单。

已有 `campaign-watch.ps1` 守护程序会监听状态文件变动，并只在最近一个任务/在线证据截止时间醒来。状态稳定时不轮询 Agent 或调用模型。部署脚本更新后需重启一次托管 watcher 才能加载新监听项。
