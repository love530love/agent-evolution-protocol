from pathlib import Path
import pytest
from agent_evolution_protocol.coordination import session_registry, wake_router
from agent_evolution_protocol.coordination.ack_tracker import open_request, acknowledge


def test_same_agent_two_live_sessions_is_blocked(tmp_path: Path):
    session_registry.register(tmp_path, agent="qoder", workspace=str(tmp_path), provider="uia", session_id="a")
    session_registry.register(tmp_path, agent="qoder", workspace=str(tmp_path), provider="uia", session_id="b")
    sent = []
    result = wake_router.route(tmp_path, agent="qoder", text="danger", event_id="e",
                               transports=[wake_router.Transport("uia", lambda p: sent.append(p) or {"ok": True})], execute=True)
    assert result["status"] == "AMBIGUOUS_SESSION"
    assert sent == []


def test_duplicate_event_and_wrong_ack_are_rejected(tmp_path: Path):
    open_request(tmp_path, event_id="e1", agent="codex")
    with pytest.raises(ValueError, match="duplicate"):
        open_request(tmp_path, event_id="e1", agent="codex")
    with pytest.raises(ValueError, match="mismatch"):
        acknowledge(tmp_path, event_id="e1", agent="qoder")
