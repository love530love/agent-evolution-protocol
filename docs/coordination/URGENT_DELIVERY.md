# 紧急指令：信箱、原会话投递、ACK 三层分开

普通 `coord-send` 只把全文写入本地信箱；**文件存在不是目标 Agent 已读**。`--kind warning` 默认立即尝试原会话投递；其他类型需要显式 `--dispatch`。确实只想留信箱时可加 `--mailbox-only`。PowerShell 7 示例：

```powershell
$env:PYTHONPATH = 'K:\PythonProjects5\agent-evolution-protocol\src'
python -m agent_evolution_protocol coord-send --from codex --to qoder --topic INCIDENT-ID --kind warning --text '指令与本地证据路径；请回复 AEP-ACK' --dispatch
```

`--dispatch` 只派发这一条，不会顺带重发其他积压任务。默认不启动客户端、不新建会话；只有显式 `--allow-launch` 且客户端启动策略也允许时，才可尝试启动。目标窗口须唯一或已锚定；匹配到的 PID 会传给 UI 适配器，跨权限读不到进程路径时仍不能泛化到别的窗口。

若该 Agent 已有**验收过**的 `coordination/ui_profiles/<agent>.json`，指挥层现在优先使用 [原会话 UI 兜底](HUMAN_UI_FALLBACK.md)：核对会话与模型标识、精确定位输入框、发送一次、用回复中才会出现的挑战串验证 ACK。没有验收档案才走原始 UIA/CDP 路由；不可凭截图猜会话标题或模型名来伪造档案。

返回的 `desktop_delivery.state` 是**投递证据级别**，不是 Agent 接单：

- `MESSAGE_VISIBLE`：原窗口读回同一事件 ID，但仍需目标自己回复 ACK。
- `CEASED`：适配器失败、无唯一输入框或发送后无法验证；不得盲目重试。读状态文件里的 `attempts[-1].receipt` 排查。
- `AGENT_ACK`：只有目标原会话的、可归属为 Agent 回复的同一事件 ID 才可升格。

例如本机 Qoder 当前可按 PID 观察到窗口，但 UIA 树没有可编辑控件；2026-10-13 的一次实投收到 `NO_RECEIPT/LAUNCH_FORBIDDEN`，已纠正为 `CEASED`，**没有证据证明它收到消息**。此时保留本地信箱并查找该客户端已验收的原生 API、CDP 或精确会话 UI 通道；不得靠坐标猜测或重复按回车。

每次紧急广播都要给每位队友单独记录：信箱 ID、桌面状态、原会话 ACK、记忆修改证据。任何一项缺失须明确标记为未验证；离线队友归队后仍需补读和回 ACK。
