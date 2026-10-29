---
name: aep-agent-onboarding
description: 接入 Agent Evolution Protocol 的会话绑定、协作、唤醒与桌面投递规范。
---

# AEP Agent 接入技能

## 先绑定，再行动

1. 确认当前工作区路径和协作根目录。
2. 读取 `coordination/` 的未读信箱、会话绑定和任务状态。
3. 认领任务并记录自己的 Agent 名称、窗口标题、进程和 PID。
4. 默认继续已有 sticky session；禁止为了绕过上下文缺失而新建会话。

## 桌面唤醒与发送

桌面消息必须按以下证据链执行：

`注册客户端 → 唯一窗口/PID 锚定 → 切换原会话 → 定位输入框 → 写入 → 定位并点击发送按钮 → 输入框清空 → 会话记录出现事件 ID → 等待 Agent ACK`

只有“输入框已写入”不能算发送成功。找不到发送按钮、窗口不唯一、会话标签不匹配或输入框未清空时，必须 `DEFERRED/CEASED`，不得回车冒充发送，不得盲目重试。

## 失败与重试

- 先保存事件 ID、错误、截图/回执和当前会话，不丢弃上下文。
- 重试前重新读取窗口和控件树；旧坐标、旧控件索引、旧截图一律失效。
- “执行连接异常”可点击原会话中的“重试”，但必须重新观察后再点击。
- 多次失败后暂停并通报，不创建新会话替代原会话。

## 本机命令规范

- Windows 默认使用 PowerShell 7 (`pwsh.exe`)。
- Python/子进程显式使用 UTF-8，并对坏字节 `errors=replace`。
- 长路径先使用 `subst` 映射短盘符。
- 所有结果写入 JSON/Markdown；关键事件同步 SQLite/FTS5。

## 记忆与协作

使用 `sqlite_memory.open_memory()` 写入事件和经验；Markdown 负责可读交接，SQLite 负责检索。DuckDB、向量库和 PowerToys 是可选增强，不得成为核心通信单点。
