# 记忆与会话存储层

协作机制采用分层存储：

1. `coordination/memory.sqlite3`：可选的本地 SQLite 事件存储，提供显式追加和检索接口；目前不会自动采集群聊、ACK、任务或经验。
2. SQLite FTS5：可用时创建辅助索引。`MemoryStore.search` 使用参数化的字面子串查询，保证中文、标点和不支持 FTS5 的环境行为一致；大库性能需另行评测。
3. JSON/Markdown：继续作为可读审计、回溯和跨 Agent 交接格式。
4. DuckDB：通过 `pip install -e '.[analytics]'` 启用，用于战绩、额度、失败率和时间序列分析。
5. 向量库：通过 `pip install -e '.[vectors]'` 启用，用于语义召回；不是核心依赖，缺失时不影响协作。

```python
from agent_evolution_protocol.coordination.sqlite_memory import open_memory
store = open_memory(coord_root)
store.append(event_id='...', agent='qoder_cn', role='assistant', text='...')
hits = store.search('发送按钮', workspace='W-aaaaaaaaaaaaaaaa')
store.close()
```

`workspace` 是精确匹配字段；省略它会跨工作区查询，空水印的旧记录也可能被返回。
重复的 `event_id` 不会插入第二次，`append` 返回 `False`；调用方需要核对重复事件是否属于同一来源。
目前并未把工作区群聊自动导入此库，也没有把该查询接入主 CLI 或 MCP；SQLite 是可选工具，
不能把文档中的设计理解为已运行的记忆同步。默认不上传会话内容；数据库、JSON 和 Markdown 均保留在本地协作区。
