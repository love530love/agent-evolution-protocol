# 成员加入事件到群聊 journal 的可重放投影

`join_journal` 将已记录的成员加入声明按明确的工作区 ID 投影为群聊 JSON 日志；来源是 `workspace_members.sqlite3` 的 `join_events`，不创建成员、不推测工作区、不校验或认证加入者。调用结果不表示 Agent 在线、已读、接单或被唤醒。

一条独立 PowerShell 命令（其中 ID 必须换成已经核验的实际工作区 ID）：

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m agent_evolution_protocol.coordination.join_journal --root 'K:\PythonProjects5\agent-evolution-protocol\coordination' --workspace-id 'W-aaaaaaaaaaaaaaaa'
```

每次处理 `--after` 后最多 `--limit`（默认 50，最多 500）条源事件，成功后返回源事件的 `next_cursor`；调用者仅在收到成功结果后自行持久化该游标。进程在投影中途退出或异常时，使用上次已保存的源游标重新调用：已发布的事件会验证原内容并复用，剩余事件继续创建。相同来源事件与房间组合使用稳定 JSON 事件 ID；如果该 ID 已被不同内容占用，操作报错并保留原文件，不覆盖。

发布先将完整 JSON 写入并 fsync 临时文件，再以硬链接原子创建目标；同一目录下并发进程不会相互替换。文件系统或权限不支持硬链接时会直接失败，不降级成可能产生半写文件的实现。SQLite 与 JSON 是两个存储，不存在跨存储原子事务；极端断电后的文件系统持久性依赖运行环境，应以源 join sequence 和实际文件重核。

**重要的消费顺序边界：** JSON 事件 ID 使用加入时间，因此延迟投影或故障恢复后的事件可能落在消费者已保存的 journal 时间游标之前；现有 `conversation_journal.read_events(after=...)` 不能保证看到这种迟到事件。消费者必须独立按工作区扫描 `workspace_members.notifications` 的 sequence 对账，或定期重扫 journal；该模块不声称自动推送或可靠通知所有消费者。`journal_projection` 是显式重建快照，投影后需重建才可搜索到新事件。默认房间为 `agent-legion`；不同 `--room` 是不同目标，产生独立投影。
