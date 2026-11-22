"""MCP readback must never label an unfiltered shared bus as a workspace."""
from agent_evolution_protocol import mcp_server
from agent_evolution_protocol.coordination import transcript_bus


def test_bound_mcp_requires_explicit_opt_in_to_unbound_shared_data(tmp_path, monkeypatch):
    workspace = "W-" + "a" * 16
    monkeypatch.setenv("AEP_COORD_ROOT", str(tmp_path))
    monkeypatch.setenv("AEP_AGENT_ID", "qoder")
    monkeypatch.setenv("AEP_WORKSPACE_ID", workspace)

    for session, message in (("session-a", "space A"), ("session-b", "space B")):
        transcript_bus.publish(tmp_path, agent="qoder", session_id=session, text=message)

    denied = mcp_server._readback()
    assert denied["status"] == "UNBOUND_TRANSCRIPT_OPT_IN_REQUIRED"
    assert denied["workspace_id"] is None
    assert denied["requested_workspace_id"] == workspace
    assert denied["workspace_filtered"] is False
    assert denied["events"] == []

    shared = mcp_server._readback(include_unbound=True)
    assert shared["status"] == "UNFILTERED_SHARED_TRANSCRIPT"
    assert shared["scope"] == "coord_root_unfiltered"
    assert shared["workspace_id"] is None
    assert shared["workspace_filtered"] is False
    assert {row["session_id"] for row in shared["events"]} == {"session-a", "session-b"}
    assert all("workspace_id" not in row for row in shared["events"])


def test_opt_in_is_required_even_without_workspace_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AEP_COORD_ROOT", str(tmp_path))
    monkeypatch.setenv("AEP_AGENT_ID", "qoder")
    monkeypatch.delenv("AEP_WORKSPACE_ID", raising=False)
    transcript_bus.publish(tmp_path, agent="qoder", text="shared")
    assert mcp_server._readback()["events"] == []
    shared = mcp_server._readback(include_unbound=True)
    assert shared["requested_workspace_id"] is None
    assert len(shared["events"]) == 1


def test_mcp_tool_exposes_explicit_opt_in(tmp_path, monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    from agent_evolution_protocol.coordination import mcp_lifecycle

    monkeypatch.setenv("AEP_COORD_ROOT", str(tmp_path))
    monkeypatch.setenv("AEP_AGENT_ID", "qoder")
    monkeypatch.setenv("AEP_WORKSPACE_ID", "W-" + "a" * 16)
    transcript_bus.publish(tmp_path, agent="qoder", text="shared")
    seen = {}

    class FakeMCP:
        def __init__(self, name):
            self.tools = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn
            return register

        def run(self):
            read = self.tools["aep_readback"]
            seen["default"] = read()
            seen["opt_in"] = read(include_unbound=True)

    fake_module = ModuleType("mcp.server.fastmcp")
    fake_module.FastMCP = FakeMCP
    monkeypatch.setitem(sys.modules, "mcp", ModuleType("mcp"))
    monkeypatch.setitem(sys.modules, "mcp.server", ModuleType("mcp.server"))
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fake_module)
    monkeypatch.setattr(mcp_lifecycle, "register", lambda *a, **k: {"pid": 1, "run_id": "test"})
    monkeypatch.setattr(mcp_lifecycle, "start_heartbeat", lambda *a, **k: (
        SimpleNamespace(set=lambda: None), SimpleNamespace(join=lambda **kwargs: None)))
    monkeypatch.setattr(mcp_lifecycle, "retire", lambda *a, **k: None)
    mcp_server.main()
    assert seen["default"]["events"] == []
    assert len(seen["opt_in"]["events"]) == 1
    assert seen["opt_in"]["workspace_id"] is None
