# 成员加入投影的持久恢复

`join_reconciler` 对每个 `(workspace_id, room)` 独立保存权威 `join_events` 的 sequence 游标；每次限量调用已有 `join_journal.project`，项目日志写成后才原子更新游标。投影写入后、游标更新前崩溃时，下次会重放同一页，已有文件必须内容一致才复用。多进程/线程使用共享文件锁，源游标记录附带事件哈希；源被恢复或篡改导致锚点不匹配时停止，不自行跳过或重置。

独立 PowerShell 命令（替换为实际已核验工作区 ID；每行独立执行）：

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m agent_evolution_protocol.coordination.join_reconciler reconcile --root 'K:\PythonProjects5\agent-evolution-protocol\coordination' --workspace-id 'W-aaaaaaaaaaaaaaaa' --limit 50
```

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m agent_evolution_protocol.coordination.join_reconciler status --root 'K:\PythonProjects5\agent-evolution-protocol\coordination' --workspace-id 'W-aaaaaaaaaaaaaaaa'
```

`SOURCE_ABSENT`、错误、损坏游标或锚点不匹配不代表队列已处理。`status` 只读保存游标，不读取源数据库或检查实时健康。调度器须显式运行、监控错误并定期重试，本模块目前没有安装或启动常驻任务。JSON 文件与 SQLite 不构成跨存储原子事务；极端断电后的持久性依赖文件系统，需对照源 sequence 和 journal。投影仍可能落在聊天时间游标之前，消费者须独立按成员序号对账；本模块不发送桌面消息，不代表成员已读、已 ACK 或 Agent 被唤醒。
