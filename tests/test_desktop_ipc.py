import pytest

from agent_evolution_protocol.coordination import desktop_ipc
from agent_evolution_protocol.coordination.workspace_identity import new_workspace_id


def test_publish_is_atomic_and_idempotent(tmp_path):
    root = tmp_path / "coord"
    workspace = new_workspace_id()
    first = desktop_ipc.publish(root, workspace_id=workspace, agent="qoder_cn",
                                session_id="s1", event_id="e1", payload={"op": "locate"})
    second = desktop_ipc.publish(root, workspace_id=workspace, agent="qoder_cn",
                                 session_id="s1", event_id="e1", payload={"op": "locate"})
    assert first["status"] == "PERSISTED"
    assert second["status"] == "DUPLICATE"
    assert not list((root / "desktop_ipc" / "events").glob("*.tmp"))


def test_event_conflict_and_receipt_are_fenced(tmp_path):
    root = tmp_path / "coord"
    workspace = new_workspace_id()
    desktop_ipc.publish(root, workspace_id=workspace, agent="workbuddy",
                        session_id="s1", event_id="e1", payload={"text": "a"})
    with pytest.raises(ValueError, match="EVENT_CONFLICT"):
        desktop_ipc.publish(root, workspace_id=workspace, agent="workbuddy",
                            session_id="s1", event_id="e1", payload={"text": "b"})
    receipt = desktop_ipc.acknowledge(root, event_id="e1", status="READY", evidence={"window": 1})
    assert receipt["status"] == "PERSISTED"
    assert desktop_ipc.acknowledge(root, event_id="e1", status="READY", evidence={"window": 1})["status"] == "DUPLICATE"


def test_state_and_recovery_survive_new_reader(tmp_path):
    root = tmp_path / "coord"
    workspace = new_workspace_id()
    desktop_ipc.set_state(root, workspace_id=workspace, agent="codebuddy_cn",
                          session_id="s1", status="READY", event_id="e2")
    desktop_ipc.publish(root, workspace_id=workspace, agent="codebuddy_cn",
                        session_id="s1", event_id="e2", kind="runner_status",
                        payload={"ready": True})
    recovered = desktop_ipc.recover(root, workspace_id=workspace, agent="codebuddy_cn")
    assert recovered["recovered"] is True
    assert recovered["state"]["status"] == "READY"
    assert recovered["events"][0]["workspace_id"] == workspace


def test_session_change_is_unknown_and_workspace_isolated(tmp_path):
    root = tmp_path / "coord"
    workspace = new_workspace_id()
    other = new_workspace_id()
    desktop_ipc.set_state(root, workspace_id=workspace, agent="qoder_cn",
                          session_id="old", status="READY")
    recovered = desktop_ipc.recover(root, workspace_id=workspace, agent="qoder_cn", session_id="new")
    assert recovered["state"]["status"] == "UNKNOWN"
    assert recovered["state"]["reason"] == "SESSION_CHANGED"
    assert desktop_ipc.read_events(root, workspace_id=other, agent="qoder_cn") == []


def test_invalid_status_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="status"):
        desktop_ipc.set_state(tmp_path / "coord", workspace_id=new_workspace_id(),
                              agent="qoder_cn", session_id="s1", status="SENT")
