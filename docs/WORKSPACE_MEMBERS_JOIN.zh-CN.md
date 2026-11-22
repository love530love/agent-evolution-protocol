# 工作区成员加入与通知：独立注册原型

`workspace_members` 为显式工作区 ID 提供成员目录和可续读的加入事件。成员行及同一加入通知在一个 SQLite 事务中保存；同一工作区与身份重复登记只产生一条记录。不同工作区即使有同名 Agent 也独立存储。

本模块已接入 `coord-workspace-members` 主 CLI，**尚未接入现有 `join-agent.ps1`、群聊或 MCP**，不会自动采集旧消息、通知桌面程序或唤醒 Agent。`source` 和 `session_id` 是调用方声明，并未认证；持有一条成员记录不证明在线、已读或已接单。工作区 ID 校验格式，但尚未验证该工作区是否存在或属于调用者；由调用方先核对已有工作区身份。

下列命令分别是一行。请以真实工作区 ID 替换示例 `W-aaaaaaaaaaaaaaaa`；路径按实际项目位置调整。

主命令可查询新成员通知（全局 `--coord-root` 置于子命令之前）：

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m agent_evolution_protocol --coord-root 'K:\PythonProjects5\agent-evolution-protocol\coordination' coord-workspace-members notifications --workspace-id 'W-aaaaaaaaaaaaaaaa' --after 0
```

也可以使用模块入口：

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m agent_evolution_protocol.coordination.workspace_members join --root 'K:\PythonProjects5\agent-evolution-protocol\coordination' --workspace-id 'W-aaaaaaaaaaaaaaaa' --agent 'codex' --source 'agent-self-report' --session-id 'example-session'
```

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m agent_evolution_protocol.coordination.workspace_members members --root 'K:\PythonProjects5\agent-evolution-protocol\coordination' --workspace-id 'W-aaaaaaaaaaaaaaaa'
```

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m agent_evolution_protocol.coordination.workspace_members notifications --root 'K:\PythonProjects5\agent-evolution-protocol\coordination' --workspace-id 'W-aaaaaaaaaaaaaaaa' --after 0 --limit 50
```

通知返回 `next_cursor`，供调用者在同一工作区继续读取；它不会修改读取游标或声称已读。首次登记会创建 `coordination/workspace_members.sqlite3`，只读查询在缺失时不会创建数据库。`join` 返回 `created=false` 表示该身份先前登记过，原始 `source`、`session_id` 和时间保持不变。

上线前还需要：接通真实加入流程并核验 workspace identity；决定从登记事务到共享聊天日志的可靠 outbox/投影和通知重试；并发跨进程验收、退出/重新加入语义、数据库迁移，以及厂商原会话是否实际接收通知的独立证据。
