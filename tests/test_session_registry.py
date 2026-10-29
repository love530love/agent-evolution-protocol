from pathlib import Path
import pytest
from agent_evolution_protocol.coordination import session_registry as sr


def test_resolution_requires_unique(tmp_path: Path):
    sr.register(tmp_path, agent="qoder-cn", workspace=str(tmp_path), provider="uia", session_id="s1")
    sr.register(tmp_path, agent="qoder-cn", workspace=str(tmp_path), provider="uia", session_id="s2")
    result = sr.resolve(tmp_path, agent="qoder-cn", workspace=str(tmp_path))
    assert result["reason"] == "AMBIGUOUS_SESSION"
    result = sr.resolve(tmp_path, agent="qoder-cn", workspace=str(tmp_path), session_id="s1")
    assert result["ok"] and result["session"]["session_id"] == "s1"


def test_ack_lifecycle(tmp_path: Path):
    sr.register(tmp_path, agent="workbuddy", workspace=str(tmp_path), provider="uia", session_id="s")
    sr.transition(tmp_path, agent="workbuddy", session_id="s", state="delivered", event_id="e1")
    row = sr.transition(tmp_path, agent="workbuddy", session_id="s", state="acknowledged", event_id="e1")
    assert row["state"] == "acknowledged"


def test_bad_state(tmp_path: Path):
    sr.register(tmp_path, agent="a", workspace=str(tmp_path), provider="x", session_id="s")
    with pytest.raises(ValueError):
        sr.transition(tmp_path, agent="a", session_id="s", state="sent")
