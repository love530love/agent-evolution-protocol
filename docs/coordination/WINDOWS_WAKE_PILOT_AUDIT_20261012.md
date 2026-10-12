# Windows 原会话唤醒试点审计（2026-10-12）

本记录区分本机实测与尚未完成的工程假设。不要把“找到窗口／运行适配器／输入框出现文本”提升为“目标 Agent 已接单”。

## 本机实测

- 从 `sbroenne/mcp-windows` 官方 `v1.3.28` release 获取 x64 自包含包；SHA-256 与官方 `SHA256SUMS.txt` 一致：`9ca69685222d3c4934680218b4a2b3b7ae5bd0c8d2ce8bc2220925f4bd924fe4`。安装在本仓库忽略的 `.tools/`，Python MCP 客户端使用隔离虚拟环境。
- MCP 初始化及工具发现成功；`window_management/find` 对 WorkBuddy 和 Qoder 各返回一个明确的窗口。WorkBuddy 的窗口截图可捕获。
- 两个客户端的 `ui_snapshot` 均只返回一个 `Window` 根节点；`ui_read` 只返回应用名称；注释截图因找不到任何 `Button/Edit/...` 控件而失败。因此 **当前会话不存在可验证的 UIA 输入框或回复区**，不能无人值守用 `ui_type` 发送。
- 本轮没有向任一 Agent 聊天框输入、发送或重启客户端。WorkBuddy 原窗口正在运行任务；当前没有可安全打断的会话切换证据。
- WorkBuddy/Qoder 进程未发现由其 PID 监听的本地 TCP 端口；不能凭固定的 9222–9241 范围把别的 Chromium 页面当作它们的 CDP 页面。

## 并行开发草稿的阻断项

以下是对当前未提交 `commander.py`、`transport_cdp.py` 和 `uia_session.ps1` 的只读审查，实施者提交前须修复并加测试：

1. `check_acks()` 在整段纯文本快照中找 `AEP-ACK:<event>`，而 `submit_order()` 已把同一 token 写进外发正文。仅自己的外发消息可造成假 ACK。需要能区分发言角色的读回证据，或把 ACK 限定为目标 Agent 自己写入的回执；不能靠纯文本包含判定。
2. `_pick_transport()` 扫描本机 CDP 端口并选第一个页面，没有绑定目标 Agent 的进程、受信端口、页面身份和会话 ID。可能把指令发进无关浏览器。默认必须拒绝未知 CDP 端口，且绑定 PID/目标与明确会话。
3. `_SUBMIT_JS` 合成 `KeyboardEvent`，不能证明 Electron 客户端接受了发送。`deliver()` 随后仅检查事件 ID 是否出现在 DOM 中，而它可能仍在未发送的输入框里；必须区分 composer 和已提交消息列表。
4. `uia_session.ps1` 的 `compose` 分支在 OCR 提示词缺失时使用 `geometry-guess` 坐标，违反“不盲点”。只可在有人监督的试验中使用截图/OCR 确认的控件；无人值守路线应 fail closed。
5. OCR 用 `CopyFromScreen` 截屏；窗口被遮挡时会读到其他应用，污染 transcript bus，甚至触发假 ACK。须先证实窗口为前台且同 PID/会话，或使用可捕获遮挡窗口的实现，并记录截图来源。
6. `_pwsh()` 降级到 `powershell.exe` 违反本机 `AGENTS.md` 默认 PowerShell 7 的要求；缺少 pwsh 时应显式失败，而不是无声回退。

## 下一道验收门槛

优先寻找客户端公开、受支持的原会话消息接口；没有则等当前任务自然结束后，**在不丢失会话**的前提下验证 Electron 可访问性/CDP 配置。每家客户端必须独立完成“原会话定位 → 发送唯一事件 ID → 从消息列表（非输入框）读回 → 目标 Agent 发独立 ACK → 读取其回复 → 写入协作事件流”。失败结果保留在待办，不自动换通道重发、不新开空白会话。
