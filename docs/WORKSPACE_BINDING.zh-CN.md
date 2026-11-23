# 工作区身份绑定原型

`workspace_binding` 将**已存在的 federation state** 的 Workspace ID 显式绑定到本机项目目录。它先通过 federation `engine.load` 检查状态存在、schema 和 ID 一致，绝不自动创建或更换工作区。绑定保存在协调根目录下的 SQLite 数据库；读绑定不创建数据库，读时再次检查 federation 状态。

下例 W-ID、项目目录和 federation 数据根均为占位，需用项目实际值替换。每行独立运行，`--federation-root` 是 federation state 的存储根，不应想当然填写项目目录：

```powershell
python -m agent_evolution_protocol.coordination.workspace_binding bind --coord-root 'K:\path\coordination' --federation-root 'K:\path\federation' --project-root 'K:\path\project' --workspace-id 'W-aaaaaaaaaaaaaaaa'
```

```powershell
python -m agent_evolution_protocol.coordination.workspace_binding lookup --coord-root 'K:\path\coordination' --project-root 'K:\path\project' --workspace-id 'W-aaaaaaaaaaaaaaaa'
```

一次绑定后同一 ID 不能悄悄改绑其他本地目录或 federation 数据根，须先设计并执行显式迁移；一个项目目录可以有多个逻辑工作区，省略 ID 查询时如果有多个则返回歧义错误。路径规范化解析符号链接并按本机操作系统规范大小写；跨机器复制或搬家后不可假设原目录自动有效。

**局限**：`join-agent.ps1` 已提供显式 `-WorkspaceId` 路径，校验现有绑定后登记成员；尚未接入 MCP、群聊投递，不会自动唤醒或派工。`engine.load` 只核对指定根目录中 state 文件的 schema 和 ID，项目目录与该 workspace 是否真实对应仍由调用方声明；两者不是一个跨文件系统和 SQLite 的原子事务。它不能认证用户或 Agent 身份，也不能证明原会话匹配。并发绑定在 `BEGIN IMMEDIATE` 后建立表并写入，遇 SQLite 短暂 locked/readonly 最多重试 5 次；历史未绑定消息不会自动归属。目录搬迁、备份还原、删除和别名迁移还需后续明确契约。
