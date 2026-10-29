"""Deterministic multi-transport wake routing.

The router is deliberately transport-agnostic: ClawChat, MCP, a local runner,
UIA and browser bridges all expose the same preview/execute contract.  It never
guesses a session and never treats delivery as acceptance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any

from . import session_registry


@dataclass(frozen=True)
class Transport:
    name: str
    send: Callable[[dict[str, Any]], dict[str, Any]]
    priority: int = 100


def route(root, *, agent: str, text: str, event_id: str, transports: list[Transport],
          workspace: str = "", session_id: str = "", conversation_id: str = "",
          window_id: str = "", execute: bool = False) -> dict[str, Any]:
    """Preview or execute one wake request against one uniquely resolved target."""
    if not execute:
        return {"status": "PREVIEW", "agent": agent, "event_id": event_id,
                "transports": [t.name for t in sorted(transports, key=lambda x: x.priority)]}
    resolved = session_registry.resolve(root, agent=agent, workspace=workspace,
                                        session_id=session_id, conversation_id=conversation_id,
                                        window_id=window_id)
    if not resolved.get("ok"):
        return {"status": resolved.get("reason", "NO_SESSION"), "agent": agent,
                "event_id": event_id, "candidates": resolved.get("candidates", [])}
    payload = {"event_id": event_id, "agent": agent, "session": resolved["session"], "text": text}
    attempts = []
    for transport in sorted(transports, key=lambda x: x.priority):
        try:
            result = dict(transport.send(payload) or {})
        except Exception as exc:  # adapters must not crash the coordinator
            result = {"status": "TRANSPORT_ERROR", "error": str(exc)}
        result.update({"transport": transport.name, "event_id": event_id})
        attempts.append(result)
        status = str(result.get("status", "")).upper()
        if status in {"ACKNOWLEDGED", "ACCEPTED"} or result.get("acknowledged") is True:
            session_registry.transition(root, agent=agent, session_id=resolved["session"]["session_id"],
                                        state="acknowledged", event_id=event_id)
            return {"status": "ACKNOWLEDGED", "event_id": event_id, "attempts": attempts}
        if status in {"SENT", "DELIVERED"} or result.get("sent") is True or result.get("ok") is True:
            session_registry.transition(root, agent=agent, session_id=resolved["session"]["session_id"],
                                        state="delivered", event_id=event_id)
            return {"status": "DELIVERED", "event_id": event_id, "attempts": attempts}
    return {"status": "FAILED", "event_id": event_id, "attempts": attempts}
