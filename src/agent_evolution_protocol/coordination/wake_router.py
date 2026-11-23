"""Deterministic multi-transport wake routing.

The router is deliberately transport-agnostic: ClawChat, MCP, a local runner,
UIA and browser bridges all expose the same preview/execute contract.  It never
guesses a session and, crucially, never promotes a transport's ``ok``/``sent``
flag into delivery.  A delivery receipt must prove that *this event* became
visible in the already-resolved original session.

Execution adapters return ordinary dictionaries.  The minimum proof for
``DELIVERED`` requires ``event_id_visible=True``, an explicit matching
``visible_event_id`` and an explicit matching ``visible_session_id``.
``ACKNOWLEDGED`` additionally requires an explicit same-session agent-ACK
receipt.  All weaker send reports stay in an intermediate/uncertain state so a
caller cannot mistake an input injection for a completed wake.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any

from ..federation.locking import LockTimeout
from . import session_registry, wake_event_fence


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

    A generic boolean or a requested session ID is not evidence of a readback.
    Adapters must report the event and session they actually observed.
    """
    return bool(event_id and session_id and result.get("event_id_visible") is True
                and result.get("visible_event_id") == event_id
                and result.get("visible_session_id") == session_id)


def _same_session_ack(result: dict[str, Any], *, event_id: str, session_id: str,
                      message_visible: bool) -> bool:
    """An adapter acknowledgement is not an agent acknowledgement by itself."""
    if not message_visible or result.get("agent_ack") is not True:
        return False
    ack_event = result.get("ack_event_id")
    ack_session = result.get("ack_session_id")
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
        or result.get("original_session_message_visible") is True
        or result.get("event_id_visible") is True
        or bool(result.get("visible_event_id"))
        or result.get("agent_ack") is True
        or status in {"SENT", "DELIVERED", "ACCEPTED", "ACKNOWLEDGED", "MESSAGE_VISIBLE"}
    )


def route(root, *, agent: str, text: str, event_id: str, transports: list[Transport],
          workspace: str = "", session_id: str = "", conversation_id: str = "",
          window_id: str = "", execute: bool = False) -> dict[str, Any]:
    """Preview or execute one wake request against one uniquely resolved target."""
    if not execute:
        return {"status": "PREVIEW", "agent": agent, "event_id": event_id,
                "transports": [t.name for t in sorted(transports, key=lambda x: x.priority)]}
    try:
        return _route_guarded(root, agent=agent, text=text, event_id=event_id,
                              transports=transports, workspace=workspace, session_id=session_id,
                              conversation_id=conversation_id, window_id=window_id)
    except LockTimeout:
        # Another caller may already have performed the write. A lock timeout
        # proves neither failed delivery nor that another route is safe.
        return {"status": "UNCERTAIN", "event_id": event_id, "attempts": [],
                "reason": "EVENT_OR_SESSION_LOCK_BUSY"}


def _route_guarded(root, *, agent: str, text: str, event_id: str,
                   transports: list[Transport], workspace: str, session_id: str,
                   conversation_id: str, window_id: str) -> dict[str, Any]:
    _, fence_path = wake_event_fence.event_paths(root, event_id)
    with wake_event_fence.acquire(root, event_id):
        previous = wake_event_fence.read(fence_path)
        if previous is not None and (previous.get("state") == "UNREADABLE"
                                     or previous.get("event_id") != event_id):
            return {"status": "UNCERTAIN", "event_id": event_id, "attempts": [],
                    "reason": "EVENT_FENCE_UNREADABLE", "already_recorded": True}
        resolved = session_registry.resolve(root, agent=agent, workspace=workspace,
                                            session_id=session_id, conversation_id=conversation_id,
                                            window_id=window_id)
        if not resolved.get("ok"):
            if previous is not None:
                return {"status": "UNCERTAIN", "event_id": event_id, "attempts": [],
                        "reason": "EVENT_RECORDED_SESSION_UNAVAILABLE", "already_recorded": True}
            return {"status": resolved.get("reason", "NO_SESSION"), "agent": agent,
                    "event_id": event_id, "candidates": resolved.get("candidates", [])}
        digest = wake_event_fence.request_hash(event_id=event_id, agent=agent, text=text,
                                                session=resolved["session"])
        if previous is not None:
            if previous.get("request_sha256") != digest:
                return {"status": "EVENT_ID_CONFLICT", "event_id": event_id,
                        "attempts": [], "already_recorded": True}
            status = previous.get("status") if previous.get("state") == "FINAL" else "UNCERTAIN"
            if status not in {"ACKNOWLEDGED", "DELIVERED", "INPUT_SUBMITTED", "FAILED", "UNCERTAIN"}:
                status = "UNCERTAIN"
            return {"status": status, "event_id": event_id, "attempts": [],
                    "reason": "EVENT_ALREADY_RECORDED", "already_recorded": True}
        # Persist before calling arbitrary adapter code. If the process dies at
        # any later point, IN_FLIGHT remains and the next caller cannot resend.
        wake_event_fence.write(fence_path, event_id=event_id, request_sha256=digest, state="IN_FLIGHT")
        result = _route_resolved(root, agent=agent, text=text, event_id=event_id,
                                 transports=transports, resolved=resolved)
        wake_event_fence.write(fence_path, event_id=event_id, request_sha256=digest,
                               state="FINAL", status=result["status"])
        return result


def _route_resolved(root, *, agent: str, text: str, event_id: str,
                    transports: list[Transport], resolved: dict) -> dict[str, Any]:
    if not resolved.get("ok"):
        return {"status": resolved.get("reason", "NO_SESSION"), "agent": agent,
                "event_id": event_id, "candidates": resolved.get("candidates", [])}
    payload = {"event_id": event_id, "agent": agent, "session": resolved["session"], "text": text}
    attempts = []
    for transport in sorted(transports, key=lambda x: x.priority):
        try:
            result = dict(transport.send(payload) or {})
        except Exception as exc:  # the adapter may have sent before failing
            result = {"status": "TRANSPORT_ERROR", "error": str(exc),
                      "transport": transport.name, "event_id": event_id}
            attempts.append(result)
            return {"status": "UNCERTAIN", "event_id": event_id, "attempts": attempts}
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
        # Only an adapter's explicit pre-input proof permits trying a second
        # transport. A generic failure/empty result can follow an actual send.
        proof = result.get("not_sent_evidence")
        if not (status == "PROVEN_NOT_SENT" and result.get("input_attempted") is False
                and isinstance(proof, str) and proof.strip()):
            return {"status": "UNCERTAIN", "event_id": event_id, "attempts": attempts}
    return {"status": "FAILED", "event_id": event_id, "attempts": attempts}
