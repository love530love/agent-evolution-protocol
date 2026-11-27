# 成员加入投影的持久恢复

`join_reconciler` 对每个 `(workspace_id, room)` 独立保存权威 `join_events` 的 sequence 游标；每次限量调用已有 `join_journal.project`，项目日志写成后才原子更新游标。投影写入后、游标更新前崩溃时，下次会重放同一页，已有文件必须内容一致才复用。多进程/线程使用共享文件锁，源游标记录附带事件哈希；源被恢复或篡改导致锚点不匹配时停止，不自行跳过或重置。

独立 PowerShell 命令（替换为实际已核验工作区 ID；每行独立执行）：

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m agent_evolution_protocol.coordination.join_reconciler reconcile --root 'K:\PythonProjects5\agent-evolution-protocol\coordination' --workspace-id 'W-aaaaaaaaaaaaaaaa' --limit 50
```

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m agent_evolution_protocol.coordination.join_reconciler status --root 'K:\PythonProjects5\agent-evolution-protocol\coordination' --workspace-id 'W-aaaaaaaaaaaaaaaa'
```

`SOURCE_ABSENT`、错误、损坏游标或锚点不匹配不代表队列已处理。`status` 只读保存游标，不读取源数据库或检查实时健康。JSON 文件与 SQLite 不构成跨存储原子事务；极端断电后的持久性依赖文件系统，需对照源 sequence 和 journal。投影仍可能落在聊天时间游标之前，消费者须独立按成员序号对账；本模块不发送桌面消息，不代表成员已读、已 ACK 或 Agent 被唤醒。

## Core 有限自动恢复

新版 Core 每轮最多扫描 1 个明确绑定的工作区，每个工作区最多投影 20 条成员事件。扫描位置持久化并循环轮换，每个工作区仍由 `join_reconciler` 保管其权威序号和校验锚点；进程在日志写入后、游标更新前退出时会幂等重放。只处理现有绑定，并重新检查其 federation 状态；无效或损坏绑定不会被猜测为其他工作区。Core 调用使用 2 秒的协作式总预算和 0.25 秒的 SQLite 忙等待与锁等待上限：操作步骤之间检查预算，超时记录错误并于后续轮次重试。文件系统与操作系统单次 I/O 无法被此机制强制中断，因此不是硬实时保证。

Core 心跳的 `join_reconciliation` 字段报告 `OK`、`NO_BINDING_STORE`、`NO_BINDINGS`、`DEGRADED` 或 `ERROR` 以及每个工作区的结果。单个工作区失败会记录错误并让其他工作区继续；扫描游标损坏或源不可读则保留错误、后续周期重试，不影响本轮任务队列处理。`SOURCE_ABSENT` 属于未处理，不能当成空队列成功。重复失败需人工检查，不自动重置游标。

上述行为只有在部署并重启到包含新代码的 Core 后才生效。没有新部署证明之前，只能称代码及隔离测试已完成；现有进程不会因仓库更新而自动加载新模块。此次变化不负责自动唤醒客户端，聊天时间游标的迟到事件仍需按成员序号对账。
