# 任一在线队友的临时指挥入口

能力弱的 Agent 不需要会操作别人的界面。它只需给出“发起者、目标、任务、指令、证据”，本地中继负责验证在线状态、持久投信箱、选唤醒适配器、去重和记录回执。最短命令：

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\scripts\ask-teammate.ps1' -From qoder -To workbuddy -Task T130 -Instruction '请独立排查这段编译错误并给出最小实验' -Evidence 'K:\project\error.txt' -SessionId '原会话 ID'
```

若 Agent 只能写文件，也可以按下面结构写入 `coordination/delegation/inbox/<id>.json`；`id` 格式为 `DYYYYMMDDTHHMMSSffffffZ-12位小写十六进制`，文件名须与 `id` 完全相同。截止时间要用带时区的 ISO 时间，且不超过 3 小时。守护程序监听新文件并处理；文件内容不可信，必须过验证。

```json
{
  "id": "D20261013T120000000000Z-123456abcdef",
  "from": "qoder",
  "to": "workbuddy",
  "task": "T130",
  "instruction": "请独立排查错误并报告最小实验",
  "evidence": ["K:\\project\\error.txt"],
  "priority": "normal",
  "session_id": "原会话 ID",
  "reply_due_at": "2026-10-13T13:00:00+08:00",
  "allow_new_session": false
}
```

发起者必须有未过期的在线证据；若该证据绑定会话 ID，请求也必须匹配。请求会写入 `delegation/receipts/<id>.json`：`WAKE_QUEUED_UNVERIFIED` 表示已排队唤醒，`RELAY_QUEUED_UNVERIFIED` 表示目标无适配器、已请一名在线且有通道的队友中继，`MAILBOX_ONLY` 表示目前只能保存消息，`REJECTED` 表示验证失败。**这四种状态都不算目标接单。** 有适配器的客户端也可能仅能聚焦窗口；应继续查看实际投递报告，不能据队列状态认定已传达。

目标队友可明确回复：

```powershell
$env:PYTHONPATH='K:\PythonProjects5\agent-evolution-protocol\src'
python -m agent_evolution_protocol.coordination.team_dispatch ack --root 'K:\PythonProjects5\agent-evolution-protocol\coordination' --event-id '<请求 ID>' --agent workbuddy --session-id '<原会话 ID>' --reply '已接单；先复现实验 A，再报告编译日志。'
```

拒绝则将 `ack` 换成 `decline` 并写明原因。回复会写回发起者信箱及共享日志；如果发起者有适配器，也会被尝试唤醒。归队、已读与任务 ACK 分开记录。默认禁止新建会话，也不允许转发请求自动上传、覆盖文件或获取超出原任务的权限。中继最多一跳，不能循环转派。

本地所有 Agent 共享同一操作系统账户和工作区，在线信号及会话 ID 是**协作一致性校验，不是抵御恶意本地 Agent 的密码学身份认证**。要跨不互信主体使用，需另加每客户端独立凭证或 OS 权限隔离。
