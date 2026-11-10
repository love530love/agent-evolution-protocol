"""Deterministic multi-transport wake routing.

The router is deliberately transport-agnostic: ClawChat, MCP, a local runner,
UIA and browser bridges all expose the same preview/execute contract.  It never
guesses a session and, crucially, never promotes a transport's ``ok``/``sent``
flag into delivery.  A delivery receipt must prove that *this event* became
visible in the already-resolved original session.

Execution adapters return ordinary dictionaries.  The minimum proof for
``DELIVERED`` is either ``original_session_message_visible=True`` or the
three-part receipt ``event_id_visible=True``, ``visible_session_id`` (or
``session_id``) equal to the resolved session id, and the event id itself.
``ACKNOWLEDGED`` additionally requires an explicit same-session agent-ACK
receipt.  All weaker send reports stay in an intermediate/uncertain state so a
caller cannot mistake an input injection for a completed wake.
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


def _status(result: dict[str, Any]) -> str:
    return str(result.get("status", "")).strip().replace("-", "_").upper()


def _visible_in_resolved_session(result: dict[str, Any], *, event_id: str,
                                 session_id: str) -> bool:
    """Return true only for an event-level, original-session readback.

    ``original_session_message_visible`` is the compact adapter contract.  The
    expanded form exists so low-level UIA/CDP adapters can surface their actual
    readback fields without inventing a higher-level flag.
    """
    if result.get("original_session_message_visible") is True:
        return True
    visible_event = (
        result.get("event_id_visible") is True
        or result.get("visible_event_id") == event_id
    )
    visible_session = result.get("visible_session_id", result.get("session_id", ""))
    return visible_event and isinstance(visible_session, str) and visible_session == session_id


def _same_session_ack(result: dict[str, Any], *, event_id: str, session_id: str,
                      message_visible: bool) -> bool:
    """An adapter acknowledgement is not an agent acknowledgement by itself."""
    if not message_visible or result.get("agent_ack") is not True:
        return False
    ack_event = result.get("ack_event_id", event_id)
    ack_session = result.get("ack_session_id", session_id)
    return ack_event == event_id and ack_session == session_id


def _input_submitted(result: dict[str, Any], status: str) -> bool:
    return (
        result.get("input_submitted") is True
        or result.get("input_verified") is True
        or result.get("composer_verified") is True
        or status in {"INPUT_SUBMITTED", "SUBMITTED_UNCONFIRMED"}
    )


def _possibly_mutated(result: dict[str, Any], status: str) -> bool:
    """Conservatively stop rather than replaying an uncertain send elsewhere."""
    return (
        result.get("ok") is True
        or result.get("sent") is True
        or result.get("submitted") is True
        or result.get("task_delivered") is True
        or status in {"SENT", "DELIVERED", "ACCEPTED", "ACKNOWLEDGED", "MESSAGE_VISIBLE"}
    )


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
        status = _status(result)
        session_id = str(resolved["session"]["session_id"])
        visible = _visible_in_resolved_session(result, event_id=event_id, session_id=session_id)
        if _same_session_ack(result, event_id=event_id, session_id=session_id,
                             message_visible=visible):
            session_registry.transition(root, agent=agent, session_id=resolved["session"]["session_id"],
                                        state="acknowledged", event_id=event_id)
            return {"status": "ACKNOWLEDGED", "event_id": event_id, "attempts": attempts}
        if visible:
            session_registry.transition(root, agent=agent, session_id=resolved["session"]["session_id"],
                                        state="delivered", event_id=event_id)
            return {"status": "DELIVERED", "event_id": event_id, "attempts": attempts}
        if _input_submitted(result, status):
            return {"status": "INPUT_SUBMITTED", "event_id": event_id, "attempts": attempts}
        if _possibly_mutated(result, status):
            return {"status": "UNCERTAIN", "event_id": event_id, "attempts": attempts}
    return {"status": "FAILED", "event_id": event_id, "attempts": attempts}
