from agent_evolution_protocol.coordination import core_host, process_identity, supervisor


def test_both_hosts_persist_their_process_creation_token(tmp_path, monkeypatch):
    token = "2026-11-24T01:02:03.123456+00:00"
    monkeypatch.setattr(process_identity, "current_started_at", lambda: token)
    core = core_host._heartbeat(tmp_path, {"status": "CYCLE_COMPLETED"})
    desktop = supervisor._heartbeat(tmp_path, {"status": "TICK_COMPLETED", "mode": "observe-only"})
    assert core["process_started_at"] == token
    assert desktop["process_started_at"] == token
    assert core["ui_touched"] is False
    assert desktop["dispatch_enabled"] is False
