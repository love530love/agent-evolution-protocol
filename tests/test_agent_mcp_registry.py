import json

import pytest

from agent_evolution_protocol.coordination import agent_mcp_registry
from agent_evolution_protocol.coordination.workspace_identity import new_workspace_id


def profile(agent="qoder_cn"):
    return {
        "agent": agent,
        "display_name": "Qoder CN",
        "client": {"kind": "desktop", "session_id": "session-qoder-cn"},
        "servers": [
            {"name": "desktop", "command": "dotnet", "args": ["WindowsMcp.dll"],
             "provider": "sbroenne", "capabilities": ["locate_session", "wake", "dispatch", "readback"]},
            {"name": "rea", "command": "rea", "provider": "rea", "capabilities": ["rea"]},
        ],
        "capabilities": ["locate_session", "wake", "dispatch", "readback", "rea"],
        "locator": {"transports": ["uia", "cdp"], "window_titles": ["Qoder"]},
        "fallbacks": ["native_api", "aep_mcp", "desktop_mcp", "uia", "mailbox"],
    }


def test_register_inventory_and_render_config(tmp_path):
    root = tmp_path / "coord"
    result = agent_mcp_registry.register(root, profile())
    assert result["registered"] is True
    inv = agent_mcp_registry.inventory(root)
    assert inv["count"] == 1
    workspace = new_workspace_id()
    rendered = agent_mcp_registry.render_config(root, "qoder_cn", workspace, coord_root=str(root))
    assert rendered["mcpServers"]["desktop"]["env"]["AEP_WORKSPACE_ID"] == workspace
    assert rendered["mcpServers"]["rea"]["env"]["AEP_AGENT_ID"] == "qoder_cn"


def test_dispatch_requires_original_session_binding(tmp_path):
    root = tmp_path / "coord"
    row = profile("workbuddy")
    row["client"] = {"kind": "desktop"}
    agent_mcp_registry.register(root, row)
    workspace = new_workspace_id()
    plan = agent_mcp_registry.plan_dispatch(root, "workbuddy", workspace, "dispatch")
    assert plan["status"] == "SESSION_BINDING_REQUIRED"
    plan = agent_mcp_registry.plan_dispatch(root, "workbuddy", workspace, "dispatch", session_id="session-1")
    assert plan["status"] == "READY_TO_DISPATCH"
    assert plan["execution_started"] is False


def test_workspace_mismatch_is_rejected(tmp_path):
    root = tmp_path / "coord"
    workspace = new_workspace_id()
    row = profile("chatgpt")
    row["workspace_id"] = workspace
    agent_mcp_registry.register(root, row)
    with pytest.raises(ValueError, match="WORKSPACE_MISMATCH"):
        agent_mcp_registry.render_config(root, "chatgpt", new_workspace_id())


def test_invalid_server_capability_is_rejected():
    row = profile()
    row["servers"][0]["capabilities"] = ["teleport"]
    with pytest.raises(ValueError, match="unknown server capabilities"):
        agent_mcp_registry.validate_profile(row)
