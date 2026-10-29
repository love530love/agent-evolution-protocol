"""Optional AEP federation MCP server.

The server is intentionally a thin bridge to the durable AEP ledger.  It does
not invent a second task database and it does not treat MCP tool completion as
Agent acceptance.  Install ``agent-evolution-protocol[desktop]`` before
running it; the core package remains usable without the MCP SDK.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _root() -> Path:
    value = os.environ.get("AEP_COORD_ROOT", ".agent-evolution/coordination")
    path = Path(value).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _agent() -> str:
    return os.environ.get("AEP_AGENT_ID", "unknown")


def _workspace() -> str:
    return os.environ.get("AEP_WORKSPACE_ID", "")


def main() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - optional runtime
        raise SystemExit("MCP SDK is not installed; install agent-evolution-protocol[desktop]") from exc

    from .coordination import agent_mcp_registry, client_registry, commander, transcript_bus

    mcp = FastMCP("AEP Federation")

    @mcp.tool()
    def aep_context() -> dict[str, Any]:
        """Return the immutable Agent/workspace binding carried by this MCP process."""
        return {"agent": _agent(), "workspace_id": _workspace(), "coord_root": str(_root())}

    @mcp.tool()
    def aep_discover(agent: str = "") -> dict[str, Any]:
        """Discover windows and session anchors without injecting input."""
        return client_registry.discover(_root(), agent)

    @mcp.tool()
    def aep_plan(operation: str, agent: str = "", session_id: str = "") -> dict[str, Any]:
        """Plan a session-aware operation; no program or message is started."""
        target = agent or _agent()
        return agent_mcp_registry.plan_dispatch(_root(), target, _workspace(), operation, session_id=session_id)

    @mcp.tool()
    def aep_launch(agent: str, allow_launch: bool = False) -> dict[str, Any]:
        """Launch a registered client only when the caller explicitly opts in."""
        return client_registry.launch(_root(), agent, allow=allow_launch)

    @mcp.tool()
    def aep_submit_order(target: str, task: str, text: str, event_id: str,
                         priority: str = "normal", allow_launch: bool = False) -> dict[str, Any]:
        """Persist one idempotent dispatch order; it is not yet a delivery receipt."""
        order = commander.submit_order(_root(), target=target, sender=_agent(), task=task, text=text,
                                       event_id=event_id, allow_launch=allow_launch, priority=priority,
                                       workspace_id=_workspace())
        return {"order": order, "execution_started": False,
                "next": "call aep_dispatch_order, then require MESSAGE_VISIBLE and AGENT_ACK"}

    @mcp.tool()
    def aep_dispatch_order(order_id: str) -> dict[str, Any]:
        """Dispatch one previously persisted order through the normal AEP adapters."""
        orders = [row for row in commander._orders(_root()) if row.get("id") == order_id]
        if len(orders) != 1:
            raise ValueError("order missing or not unique; no input attempted")
        return commander.dispatch(_root(), order=orders[0])

    @mcp.tool()
    def aep_readback(agent: str = "", limit: int = 50) -> dict[str, Any]:
        """Read the shared transcript for evidence; this is not an ACK."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be 1..500")
        target = agent or _agent()
        return {"agent": target, "workspace_id": _workspace(),
                "events": transcript_bus.read_events(_root(), agent=target, limit=limit)}

    mcp.run()


if __name__ == "__main__":  # pragma: no cover - optional runtime
    main()
