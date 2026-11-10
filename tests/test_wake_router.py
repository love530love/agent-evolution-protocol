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
        return {"status": "message-visible", "event_id_visible": True,
                "visible_session_id": p["session"]["session_id"]}
    result = wake_router.route(tmp_path, agent="qoder", text="x", event_id="e1",
                               transports=[wake_router.Transport("uia", send)], execute=True)
    assert result["status"] == "DELIVERED"
    assert calls == ["e1"]
    assert session_registry.resolve(tmp_path, agent="qoder")["session"]["state"] == "delivered"


def test_router_does_not_promote_generic_ok_to_delivery(tmp_path: Path):
    session_registry.register(tmp_path, agent="qoder", workspace=str(tmp_path),
                              provider="uia", session_id="s1")
    result = wake_router.route(
        tmp_path, agent="qoder", text="x", event_id="e1",
        transports=[wake_router.Transport("uia", lambda _: {"ok": True})],
        execute=True,
    )
    assert result["status"] == "UNCERTAIN"
    assert session_registry.resolve(tmp_path, agent="qoder")["session"]["state"] == "registered"


def test_router_keeps_verified_input_as_intermediate_state(tmp_path: Path):
    session_registry.register(tmp_path, agent="qoder", workspace=str(tmp_path),
                              provider="uia", session_id="s1")
    result = wake_router.route(
        tmp_path, agent="qoder", text="x", event_id="e1",
        transports=[wake_router.Transport("uia", lambda _: {"input_verified": True,
                                                               "status": "submitted-unconfirmed"})],
        execute=True,
    )
    assert result["status"] == "INPUT_SUBMITTED"
    assert session_registry.resolve(tmp_path, agent="qoder")["session"]["state"] == "registered"


def test_router_requires_same_session_event_readback_for_delivery(tmp_path: Path):
    session_registry.register(tmp_path, agent="qoder", workspace=str(tmp_path),
                              provider="uia", session_id="s1")
    result = wake_router.route(
        tmp_path, agent="qoder", text="x", event_id="e1",
        transports=[wake_router.Transport("uia", lambda _: {
            "task_delivered": True, "event_id_visible": True,
            "visible_session_id": "wrong-session",
        })],
        execute=True,
    )
    assert result["status"] == "UNCERTAIN"
    assert session_registry.resolve(tmp_path, agent="qoder")["session"]["state"] == "registered"


def test_router_requires_explicit_same_session_agent_ack(tmp_path: Path):
    session_registry.register(tmp_path, agent="qoder", workspace=str(tmp_path),
                              provider="uia", session_id="s1")
    result = wake_router.route(
        tmp_path, agent="qoder", text="x", event_id="e1",
        transports=[wake_router.Transport("uia", lambda _: {
            "event_id_visible": True, "visible_session_id": "s1",
            "agent_ack": True, "ack_event_id": "e1", "ack_session_id": "s1",
        })],
        execute=True,
    )
    assert result["status"] == "ACKNOWLEDGED"
    assert session_registry.resolve(tmp_path, agent="qoder")["session"]["state"] == "acknowledged"


def test_router_does_not_treat_adapter_acknowledged_as_agent_ack(tmp_path: Path):
    session_registry.register(tmp_path, agent="qoder", workspace=str(tmp_path),
                              provider="uia", session_id="s1")
    result = wake_router.route(
        tmp_path, agent="qoder", text="x", event_id="e1",
        transports=[wake_router.Transport("uia", lambda _: {"status": "ACKNOWLEDGED"})],
        execute=True,
    )
    assert result["status"] == "UNCERTAIN"
    assert session_registry.resolve(tmp_path, agent="qoder")["session"]["state"] == "registered"
