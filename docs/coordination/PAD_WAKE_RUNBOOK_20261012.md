# PAD 原会话唤醒通道：接入与验收

## 当前落地状态

- 已确认本机 Microsoft Store 版 PAD `11.2610.155.0` 正在运行，`ms-powerautomate:` 已注册。
- `wake_adapters/pad_flow.ps1` 已可从既有 `wake_daemon.py` 路径调用；配置 `AEP_PAD_WORKBUDDY_FLOW_ID` 或 `AEP_PAD_QODER_FLOW_ID` 后，对应适配器优先触发 PAD 流程。未配置时保持原窗口适配器；只聚焦，不冒称发送。
- 触发后等待同事件 ID 的回执；回执 `message-visible` 才标记 `task_delivered=true`，仍不等于 Agent ACK。回执缺失/不明时 **不自动改走另一条输入路线**，必须先读回原会话，防止双发。
- 本轮只有 `-BuildOnly` 与拒绝错目标的本机测试，**尚未创建实际 PAD 流程，未向任何 Agent UI 发消息**。电脑控制桥初始化失败，不能安全地替用户在设计器里录制流程。

## PAD 流程契约（每个 Agent 一条）

在 PAD 中以稳定 ID 创建流程并声明四个文本输入：`WakeFile`、`EventId`、`Agent`、`ReceiptFile`。流程只允许：

1. 读取 `WakeFile`，验证 `type=wake_request`、`id=EventId`、`to=Agent`，且未过期、目标会话被信任。读取本地摘要/证据，而不是把所有上下文塞进 URL。
2. 用 UIA 精确定位目标应用的**现有**唯一窗口和正确会话；找不到、窗口不唯一、会话不符即返回 `not-delivered`，不创建新会话。
3. 在已观察的聊天输入框输入一条短消息：`[AEP:<EventId>] 请查看本地信箱 <WakeFile> 并在阅读后 ACK；不得自动上传。` 发送前再次核对目标窗口、会话与事件 ID。
4. 发送后从同一会话**读回可见文本中的相同事件 ID**。仅在可见时把 `{"event_id":"...","agent":"...","status":"message-visible"}` 写入 `ReceiptFile`；明确未送达写 `not-delivered`；任何状态不明写 `uncertain`。回执最好先写临时文件再原子改名。不要替 Agent 自动写 ACK。

安全要求：不要把无法辨认的 UI 控件改成坐标盲点；不要处理登录、授权或安全提示；不要关闭 PAD 的外部触发确认来冒充无人值守。微软文档指出该确认默认开启，并且运行 URL 的外部触发有许可前提；先核对当前帐号的授权和 PAD 流程是否已存在。<https://learn.microsoft.com/en-us/power-automate/desktop-flows/run-desktop-flows-url-shortcuts>

## 开关和演练

取得 PAD 流程 Properties 中的真实 GUID 后，才设置例如 `$env:AEP_PAD_WORKBUDDY_FLOW_ID='<GUID>'`。不要用流程名称猜 ID。演练阶段先对一个无害的测试事件运行 `pad_flow.ps1 -BuildOnly`，只验证事件、协议注册和目标回执路径，**不会触发 PAD**。之后进行一次有人监督的真发送：记录 PAD 运行日志、原会话截图/文字、同 ID 回执和目标 Agent 自行 ACK。两次以上不同事件通过且无新会话，才能考虑后台监听队列。

## 多通道状态机

`QUEUED → NATIVE_SENT / PAD_TRIGGERED / WINDOW_FOCUSED → MESSAGE_VISIBLE → AGENT_ACK → CLAIMED → RESULT_VERIFIED`。`PAD_TRIGGERED` 和 `WINDOW_FOCUSED` 均不能提升为 `MESSAGE_VISIBLE`。只有明确 `not-delivered` 且确认**没有输入**时才允许改用另一通道；超时或 `uncertain` 保持待核，不自动重试。桌面焦点是独占资源，PAD 本地 attended 同时只能运行一条流程；不同 Agent 的信箱可并行写，但 UI 投递要排队。
