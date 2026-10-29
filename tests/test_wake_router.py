from pathlib import Path

from agent_evolution_protocol.coordination import session_registry, wake_router


def test_router_requires_unique_session(tmp_path: Path):
    result = wake_router.route(tmp_path, agent="qoder", text="x", event_id="e1",
                               transports=[], execute=True)
    assert result["status"] == "NO_SESSION"


def test_router_preview_does_not_send(tmp_path: Path):
    called = []
    session_registry.register(tmp_path, agent="qoder", workspace=str(tmp_path),
                              provider="uia", session_id="s1")
    t = wake_router.Transport("uia", lambda p: called.append(p) or {"ok": True})
    result = wake_router.route(tmp_path, agent="qoder", text="x", event_id="e1",
                               transports=[t])
    assert result["status"] == "PREVIEW"
    assert not called


def test_router_records_delivery_and_stops_on_first_success(tmp_path: Path):
    session_registry.register(tmp_path, agent="qoder", workspace=str(tmp_path),
                              provider="uia", session_id="s1")
    calls = []
    def send(p):
        calls.append(p["event_id"])
        return {"status": "DELIVERED"}
    result = wake_router.route(tmp_path, agent="qoder", text="x", event_id="e1",
                               transports=[wake_router.Transport("uia", send)], execute=True)
    assert result["status"] == "DELIVERED"
    assert calls == ["e1"]
    assert session_registry.resolve(tmp_path, agent="qoder")["session"]["state"] == "delivered"
