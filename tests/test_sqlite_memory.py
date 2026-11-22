from agent_evolution_protocol.coordination.sqlite_memory import MemoryStore


def test_memory_store_roundtrip_and_search(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    assert store.append(event_id="e1", agent="qoder_cn", role="user", text="昆仑回表失败")
    assert store.append(event_id="e2", agent="workbuddy", role="assistant", text="已修复发送按钮")
    rows = store.search("发送按钮")
    assert rows and rows[0]["event_id"] == "e2"
    assert store.search("昆仑", agent="qoder_cn")[0]["event_id"] == "e1"
    store.close()


def test_search_is_literal_and_can_scope_workspace(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    try:
        store.append(event_id="a", agent="qoder", role="assistant", workspace="W-a", text="问题 A-1：发送失败 (50%)")
        store.append(event_id="b", agent="qoder", role="assistant", workspace="W-b", text="问题 A-1：发送失败")
        store.append(event_id="c", agent="qoder", role="assistant", text="问题 A-1：发送失败")
        assert [row["event_id"] for row in store.search("a-1", workspace="W-a")] == ["a"]
        assert [row["event_id"] for row in store.search("(50%)", workspace="W-a")] == ["a"]
        assert store.search("_", workspace="W-a") == []
        assert len(store.search("发送失败")) == 3
        store.fts_enabled = False
        assert [row["event_id"] for row in store.search("A-1", workspace="W-b")] == ["b"]
    finally:
        store.close()


def test_duplicate_event_id_returns_false_instead_of_claiming_insert(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    try:
        assert store.append(event_id="same", agent="a", role="user", text="one", workspace="W-a")
        assert not store.append(event_id="same", agent="b", role="user", text="two", workspace="W-b")
        assert store.search("two", workspace="W-b") == []
    finally:
        store.close()


def test_search_rejects_invalid_limit(tmp_path):
    import pytest
    store = MemoryStore(tmp_path / "memory.sqlite3")
    try:
        for limit in (0, 501, True):
            with pytest.raises(ValueError):
                store.search("x", limit=limit)
    finally:
        store.close()
