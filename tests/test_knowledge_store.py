from agent_evolution_protocol.coordination.knowledge_store import add, search

def test_structured_memory_round_trip(tmp_path):
    path = tmp_path / "memory.sqlite"
    add(path, kind="lesson", task_id="T1", actor="qoder", title="DPI", body="Use UIA anchor", tags="windows,uia")
    rows = search(path, query="DPI", kind="lesson")
    assert rows[0]["task_id"] == "T1"
