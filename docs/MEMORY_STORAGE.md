# 记忆与会话存储层

协作机制采用分层存储：

1. `coordination/memory.sqlite3`：必选本地 SQLite，保存事件、会话、ACK、任务和经验索引。
2. SQLite FTS5：可用时提供全文检索；不支持 FTS5 的环境自动降级为 `LIKE` 查询。
3. JSON/Markdown：继续作为可读审计、回溯和跨 Agent 交接格式。
4. DuckDB：通过 `pip install -e '.[analytics]'` 启用，用于战绩、额度、失败率和时间序列分析。
5. 向量库：通过 `pip install -e '.[vectors]'` 启用，用于语义召回；不是核心依赖，缺失时不影响协作。

```python
from agent_evolution_protocol.coordination.sqlite_memory import open_memory
store = open_memory(coord_root)
store.append(event_id='...', agent='qoder_cn', role='assistant', text='...')
hits = store.search('发送按钮')
```

默认不上传会话内容；数据库、JSON 和 Markdown 均保留在本地协作区。
