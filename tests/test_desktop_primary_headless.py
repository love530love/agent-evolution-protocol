"""Contract tests for desktop-primary/headless-augmentation routing.

These tests never launch a real client or inject desktop input.  They prove the
state gates that make a future live acceptance safe: identity binding before
dispatch, ambiguity blocking, and evidence-graded readback.
"""
from pathlib import Path
from unittest.mock import patch

from agent_evolution_protocol.coordination import agent_mcp_registry, client_registry, delivery_audit
from agent_evolution_protocol.coordination.workspace_identity import new_workspace_id


def _profile(agent="workbuddy"):
    return {
        "agent": agent,
        "display_name": agent,
        "client": {"kind": "desktop"},
        "servers": [
            {"name": "aep", "command": "python", "provider": "aep-federation",
             "capabilities": ["locate_session", "wake", "dispatch", "readback"]},
        ],
        "capabilities": ["locate_session", "wake", "dispatch", "readback"],
        "locator": {"transports": ["aep_mcp", "desktop_mcp", "uia", "mailbox"],
                    "window_titles": [agent]},
        "fallbacks": ["aep_mcp", "desktop_mcp", "uia", "mailbox"],
    }


def test_headless_plan_is_augmentation_and_requires_session_anchor(tmp_path):
    root = tmp_path / "coord"
    agent_mcp_registry.register(root, _profile())
    workspace = new_workspace_id()
    result = agent_mcp_registry.plan_dispatch(root, "workbuddy", workspace, "dispatch")
    assert result["status"] == "SESSION_BINDING_REQUIRED"
    bound = agent_mcp_registry.plan_dispatch(root, "workbuddy", workspace, "dispatch", session_id="pid:321")
    assert bound["status"] == "READY_TO_DISPATCH"
    assert bound["channels"] == ["aep_mcp", "desktop_mcp", "uia", "mailbox"]
    assert bound["execution_started"] is False


def test_two_same_title_windows_block_until_anchor(tmp_path):
    root = tmp_path / "coord"
    with patch.object(client_registry, "visible_windows", return_value=[
        {"hwnd": 1, "pid": 10, "title": "WorkBuddy"},
        {"hwnd": 2, "pid": 11, "title": "WorkBuddy"},
    ]):
        discovered = client_registry.discover(root, "workbuddy")
    row = discovered["clients"][0]
    assert row["unique"] is False
    assert row["anchored"] is False
    assert "cannot be targeted safely" in discovered["note"]

    client_registry.set_anchor(root, agent="workbuddy", pid=11, title="WorkBuddy", session_id="session-11")
    with patch.object(client_registry, "visible_windows", return_value=[
        {"hwnd": 1, "pid": 10, "title": "WorkBuddy"},
        {"hwnd": 2, "pid": 11, "title": "WorkBuddy"},
    ]):
        anchored = client_registry.discover(root, "workbuddy")["clients"][0]
    assert anchored["unique"] is False
    assert anchored["anchored"] is True
    assert any(item["is_anchor"] and item["pid"] == 11 for item in anchored["windows"])


def test_unknown_delivery_requires_inspection_not_retry(tmp_path):
    root = tmp_path / "coord"
    message = root / "messages" / "workbuddy" / "evt-1.json"
    message.parent.mkdir(parents=True)
    message.write_text('{"id":"evt-1","to":"workbuddy","topic":"wake","kind":"task"}\n', encoding="utf-8")
    state = root / "commander" / "state" / "order-1.json"
    state.parent.mkdir(parents=True)
    state.write_text('{"id":"order-1","target":"workbuddy","event_id":"evt-1","state":"UNKNOWN",'
                     '"attempts":[{"note":"transport outcome unknown"}]}\n', encoding="utf-8")
    result = delivery_audit.audit(root, agent="workbuddy")
    assert result["rows"][0]["next_required"] == "INSPECT_UNKNOWN"
    assert result["rows"][0]["agent_accepted"] is False
    assert result["rows"][0]["mailbox_read"] is False


def test_restart_like_reload_preserves_workspace_and_server_bindings(tmp_path):
    root = tmp_path / "coord"
    workspace = new_workspace_id()
    row = _profile("qoder_cn")
    row["workspace_id"] = workspace
    agent_mcp_registry.register(root, row)
    # A new process would load from the same durable profile directory.
    reloaded = agent_mcp_registry.load(root, "qoder_cn")
    assert reloaded["workspace_id"] == workspace
    assert reloaded["servers"][0]["provider"] == "aep-federation"
