from agent_evolution_protocol.coordination.observability import span

def test_jsonl_fallback(tmp_path):
    path = tmp_path / "trace.jsonl"
    with span("wake", attributes={"agent": "qoder"}, log_path=path): pass
    if path.exists(): assert '"name": "wake"' in path.read_text(encoding="utf-8")
