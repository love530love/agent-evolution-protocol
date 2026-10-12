# 原会话唤醒与指挥：传输层选择（2026-10-12）

## 结论

采用 **受支持的原会话 API/协议优先，桌面 UI Automation 兜底** 的混合传输；本地事件、权限、租约、ACK 与审计仍由 AEP 统一管理。任何一种传输都不能自称“无限制”：客户端的账号权限、会话隔离、确认弹窗和反自动化边界必须保留。桌面模拟输入不应成为绕过安全机制的手段。

| 层 | 可做什么 | 成功证据 | 失败降级 |
|---|---|---|---|
| 原生会话 API／官方命令 | 按稳定 conversation/thread ID 发送、读取 | 服务端回执 + 对方同 ID 消息 | API 不支持则尝试受控 UI |
| 受控 UI Automation（MCP/CLI） | 定位已打开的原窗口、原会话、输入框，提交短消息 | 输入前后界面证据 + 对话中可读到相同事件 ID | 控件不可辨、窗口不唯一即停止 |
| 截图/OCR/坐标 | 仅在 UIA 无法读取、有人监督的短试点中使用 | 前后截图及人工核对 | 不做无人值守指挥 |
| 文件信箱 | 不依赖窗口；保存完整任务与证据 | 文件持久化 | 只是 deposited，不是对方已读 |

PAD 可作为 UI 执行器。**2026-10-12 已纠正安装探测**：本机是 Microsoft Store 版 `Microsoft.PowerAutomateDesktop 11.2610.155.0`，安装在 `C:\Program Files\WindowsApps\Microsoft.PowerAutomateDesktop_11.2610.155.0_x64__8wekyb3d8bbwe`；`PAD.Console.Host`、`PAD.Designer`、`PAD.BridgeToUIAutomation2` 正在运行，`ms-powerautomate:` 协议已注册。此前只查 MSI 默认路径导致“未发现安装”的误判。开源候选为 `sbroenne/mcp-windows`、Microsoft AutoGenesis `pywinauto-mcp-server`；先审许可证、能力范围和本机兼容性，再决定引入，不把第三方工具直接放到高权限主通道。Flue 现有适配器不覆盖 WorkBuddy/Qoder 等 Agent 客户端。

Microsoft 官方支持 `ms-powerautomate:/console/flow/run?workflowId=...` 或 `workflowName=...`，可传文本等输入参数和 `runId` 用于日志关联。但外部触发默认弹确认；官方文档列出 Premium / pay-as-you-go 前提，且本地 attended 同时只运行一个流程。**不能为了“无人值守”擅自关闭确认或修改安全设置**；先查用户现有许可与流程配置，并验证有权在该环境中外部触发。参考：<https://learn.microsoft.com/en-us/power-automate/desktop-flows/run-desktop-flows-url-shortcuts>。

## 调度契约

1. 指挥层只发结构化事件：`event_id`、`target_agent`、`target_session_id`、`priority`、`task`、`digest_path`、`requested_action`、`expires_at`。消息正文简短，详情在本地摘要和证据文件中。**优先级只决定 AEP 队列次序，不代表可抢占正在运行的模型回合。**
2. 每目标会话只有一个 UI 输入租约；可并行准备不同任务，但同一桌面焦点和输入通道串行。紧急停火可排在普通唤醒前；若不能确认是否已发出，不自动重发，先读回同一 `event_id`。
3. 传输状态依次为 `DEPOSITED` → `WINDOW_FOUND` → `INPUT_SUBMITTED` → `MESSAGE_VISIBLE` → `AGENT_ACK` → `CLAIMED` → `RESULT_VERIFIED`。不得跳级推断；超时或失败保留待办并通知 Deputy/用户。
4. 原会话找不到、同名窗口不唯一、会话 ID 不匹配、登录态变化、焦点被别的程序抢走或 UI 控件不可辨时，**禁止输入**；不能靠新建窗口凑数。新会话只能由用户明确允许并附完整交接。
5. 任何 Agent 对另一 Agent 的消息均带来源、任务、事件 ID 与可核对证据。接收端按自身权限判断是否执行；不接受“收到消息就自动上传/删除/改权限”。

## 本机只读探测

现有 `existing_window.ps1 -ObserveOnly` 对 WorkBuddy 找到唯一窗口（PID 26712，`D:\Programs\WorkBuddy\WorkBuddy.exe`），但报告 `task_delivered=false`、`agent_accepted=false`。Qoder 按当前适配器固定路径匹配得到 0 个窗口；虽有 Qoder 进程，不能据此猜测目标。当前 WorkBuddy 适配器只做聚焦，不向聊天框输入，因此“adapter-executed”不是实际唤醒。

## 最小试点及通过条件

先核实目标原会话的稳定 ID 和 UIA 可读输入框；然后仅发送一条无害、带唯一 `event_id` 的协作测试消息。观察原会话出现同 ID 消息、由目标 Agent 自己回写 ACK、不会开新窗口；重复同 ID 不重复发送。任一步证据缺失即停在相应状态，不向下一家 Agent 扩展。整个过程不使用真实赛题上传额度。
