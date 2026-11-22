# 群聊 JSON 的 SQLite 可重建索引

`journal_projection` 是对 `coordination/private_conversations/events/*.json` 的**显式重建快照**。
JSON 原文仍是事实来源，索引不会写回 JSON、唤醒 Agent、证明已读或自动生成 ACK；
它只采集已进入该 journal 的内容，不会抓取各厂商所有私人会话。

在项目根目录用一条命令重建（默认索引位置为协调根下的 `journal-index.sqlite3`）：

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m agent_evolution_protocol.coordination.journal_projection rebuild --coord-root 'K:\PythonProjects5\agent-evolution-protocol\coordination'
```

也可以使用统一主命令（每行独立执行）：

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m agent_evolution_protocol --coord-root 'K:\PythonProjects5\agent-evolution-protocol\coordination' coord-journal-index rebuild
```

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m agent_evolution_protocol --coord-root 'K:\PythonProjects5\agent-evolution-protocol\coordination' coord-journal-index search --workspace-id 'W-aaaaaaaaaaaaaaaa' --contains '进展'
```

按实际工作区 ID 查询（下例 ID 为占位符，不是本机项目 ID）：

```powershell
& 'K:\PythonProjects5\agent-evolution-protocol\.tools\agent-runtime\Scripts\python.exe' -m agent_evolution_protocol.coordination.journal_projection search --coord-root 'K:\PythonProjects5\agent-evolution-protocol\coordination' --workspace-id 'W-aaaaaaaaaaaaaaaa' --room 'agent-legion' --contains 'A-1' --limit 50
```

如果要核对旧的未标记事件，必须显式使用 `--unbound`，它与 `--workspace-id` 互斥，
输出注明 `scope=unbound`，不会自动推定或修改任何事件的归属。输出包含事件 ID、
事件时间、actor/source、源文件路径与原始内容 SHA-256、索引时间和分页 `next_cursor`。
`--after` 是排他游标；检索是参数化的字面子串查询，中文、标点和 `%` 不作为 SQL 语法。

重建使用单个 SQLite 事务：失败回滚后保留上一次完整快照；重复重建不重复记录，源文件
删除后下次重建会从投影移除。损坏或字段无效的 JSON 不进入投影，报告其路径和原因。
源目录缺失时不会清空现有索引。索引的查询返回上次 `indexed_at`，不是当前文件自动同步
的保证；可据源路径和哈希重新核对。

使用 `status --coord-root <协调目录>` 可以只读查看索引时间、未绑定数量及最多 50 条
损坏文件路径和原因；`--limit` 可调整报告上限，最多 500 条。状态本身不会检查索引后源文件是否变化。

## 边界

- 主 CLI 已接入；尚未接 MCP、后台增量监听或正式成员加入登记；`--unbound` 不代表可直接分配归属。
- 未匹配或无标签的旧事件不会混入指定工作区，避免多会话串台。
- 搜索以 SQLite `instr(lower(...))` 实现，默认 lower 的非 ASCII 大小写折叠由 SQLite 决定，
  大型历史库尚无性能评估；这是基本 SQL 查询，不声称语义检索或 FTS 加速。
- 索引包含群聊内容，应与协调区本身一样对待；默认只写本地，不同步到外部服务。
