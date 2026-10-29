from agent_evolution_protocol.coordination.sqlite_memory import MemoryStore


def test_memory_store_roundtrip_and_search(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    assert store.append(event_id="e1", agent="qoder_cn", role="user", text="昆仑回表失败")
    assert store.append(event_id="e2", agent="workbuddy", role="assistant", text="已修复发送按钮")
    rows = store.search("发送按钮")
    assert rows and rows[0]["event_id"] == "e2"
    assert store.search("昆仑", agent="qoder_cn")[0]["event_id"] == "e1"
    store.close()
