# 任意 Agent 的可验证接单闭环

这条通道面向只能进入共享工作区、能运行一条命令的 Agent。它不要求 Agent 具备浏览器、桌面自动化或专用 MCP；也不把“窗口聚焦”“文字已填入”或“消息文件已出现”误报成接单成功。

## 最短约定

对接新 Agent 时只需给它这句话：

> 你已加入本工作区。先运行 `handoff_worker --action inbox` 查看发给你的任务；对每个任务写一份真实分析到 Markdown，然后用同一事件 ID 执行 `--action accept`。回执必须包含分析文件的 SHA-256；不要以“已阅”代替结论。

若它在 Windows/PowerShell 7 上，直接给它更短的一句即可：

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\scripts\join-agent.ps1' -Agent '<唯一_agent_id>' -SessionId '<当前原会话 ID>'
```

这会登记在线状态并列出仅属于它的未完成任务；不会新建会话、不会轮询，也不会把在线登记当作接单。

## 接单方的两步

在仓库根目录执行（将 `my_agent` 换成唯一名字）：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m agent_evolution_protocol.coordination.handoff_worker `
  --action inbox --root "$PWD\coordination" `
  --bridge-root "$PWD\src\agent_evolution_protocol\coordination" --agent my_agent
```

选择一个 `event_id` 后，先产出自己的分析，例如 `coordination\my_analysis.md`，再提交：

```powershell
python -m agent_evolution_protocol.coordination.handoff_worker `
  --action accept --root "$PWD\coordination" `
  --bridge-root "$PWD\src\agent_evolution_protocol\coordination" `
  --agent my_agent --event-id '<event_id>' --session-id '<原会话 ID>' `
  --analysis-file "$PWD\coordination\my_analysis.md"
```

成功条件是：

1. `coordination/delegation/receipts/<event_id>.json` 为 `AGENT_ACK`；
2. `coordination/delegation/artifacts/<event_id>/<agent>.json` 存在，且含分析正文和 SHA-256；
3. 发起方信箱出现 `<event_id>-reply.json`，并含同一哈希。

任何一项缺失都只表示派发、投递或草稿阶段，**不是**闭环完成。

## 发起方

发起方继续使用 `scripts/ask-teammate.ps1` 或 `team_dispatch request`。派单器会写入定向信箱、按已注册适配器请求唤醒，并保持原会话策略；接收端无桌面能力时仍可通过本页的文件闭环交付分析。

## 推广边界

该协议可统一接入 Codex、Qoder、WorkBuddy、Claude、Hermes 或其他工具，但名称必须唯一。桌面/底层适配器只负责把 Agent 叫醒并把短消息送到既有会话；它们必须驱动上述两步，而不能用 UI 截图或“已输入”替代 `AGENT_ACK`。对无法运行命令的 Agent，保留人工或专用 UI 适配器；在真正写出同一回执前状态仍是未验证。

本机已发现但 UIA 树不完整的 Qoder CN、CodeBuddy CN 与 WorkBuddy AI 也有“唯一原窗口聚焦”适配器。它只在窗口唯一时尝试唤醒，并明确标记 `window-focused-no-delivery`；由于这不是发送或阅读证明，接单仍须走本页的分析回执。

## 验收

```powershell
python -m pytest -q tests/test_handoff_worker.py tests/test_team_dispatch.py
```

这会验证：定向派单、接收端分析归档、哈希绑定回执，以及拒绝错误目标的情况。它是协议验收，不是外部桌面产品已经回话的证明。
