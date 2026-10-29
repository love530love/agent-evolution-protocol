"""Durable Supervisor <-> Desktop Runner IPC journal.

This module is intentionally transport-neutral: it persists commands, runner
status and receipts in an atomic local journal, but never launches a process
or touches a window.  A Supervisor and a Desktop Runner can therefore be
restarted independently and reconstruct the last known state without turning
an old message into a new desktop action.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from .workspace_identity import validate_workspace_id


STATUS = {"READY", "UNAVAILABLE", "UNKNOWN"}
EVENT_KIND = {"runner_command", "runner_status", "runner_receipt"}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SAFE_AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_agent(value: str) -> str:
    if not isinstance(value, str) or not SAFE_AGENT.fullmatch(value):
        raise ValueError("invalid agent")
    return value


def _validate_event(value: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError("invalid event_id")
    return value


def _root(root: Path) -> Path:
    value = Path(root).resolve() / "desktop_ipc"
    for name in ("events", "receipts", "states"):
        (value / name).mkdir(parents=True, exist_ok=True)
    return value


def _atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write and replace one JSON record atomically with a flush barrier."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid IPC record: {path.name}") from exc
    if not isinstance(data, dict):
        raise ValueError("IPC record must be an object")
    return data


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _identity(*, workspace_id: str, agent: str, session_id: str, event_id: str) -> dict[str, str]:
    validate_workspace_id(workspace_id)
    _validate_agent(agent)
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id is required")
    _validate_event(event_id)
    return {"workspace_id": workspace_id, "agent": agent,
            "session_id": session_id.strip(), "event_id": event_id}


def publish(root: Path, *, workspace_id: str, agent: str, session_id: str,
            event_id: str, kind: str = "runner_command",
            payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist one Supervisor/Runner event; same event is idempotent."""
    identity = _identity(workspace_id=workspace_id, agent=agent,
                         session_id=session_id, event_id=event_id)
    if kind not in EVENT_KIND:
        raise ValueError(f"kind must be one of {sorted(EVENT_KIND)}")
    body = dict(payload or {})
    event = {"schema_version": 1, "type": kind, **identity,
             "payload": body, "created_at": _now()}
    path = _root(root) / "events" / f"{event_id}.json"
    if path.is_file():
        previous = _read(path)
        if _fingerprint({k: previous.get(k) for k in ("schema_version", "type", *identity, "payload")}) != \
                _fingerprint({k: event.get(k) for k in ("schema_version", "type", *identity, "payload")}):
            raise ValueError("EVENT_CONFLICT")
        return {"event_id": event_id, "status": "DUPLICATE", "event": previous,
                "execution_started": False}
    _atomic(path, event)
    return {"event_id": event_id, "status": "PERSISTED", "event": event,
            "execution_started": False}


def set_state(root: Path, *, workspace_id: str, agent: str, session_id: str,
              status: str, reason: str = "", event_id: str = "",
              evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist the last known runner state for one workspace/agent/session."""
    validate_workspace_id(workspace_id)
    _validate_agent(agent)
    if status not in STATUS:
        raise ValueError("status must be READY, UNAVAILABLE or UNKNOWN")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id is required")
    event_id = event_id or ("IPC-" + uuid4().hex[:16])
    _validate_event(event_id)
    state = {"schema_version": 1, "workspace_id": workspace_id, "agent": agent,
             "session_id": session_id.strip(), "event_id": event_id, "status": status,
             "reason": str(reason), "evidence": dict(evidence or {}), "updated_at": _now()}
    key = hashlib.sha256(f"{workspace_id}:{agent}".encode("utf-8")).hexdigest()[:24]
    _atomic(_root(root) / "states" / f"{key}.json", state)
    return state


def read_state(root: Path, *, workspace_id: str, agent: str) -> dict[str, Any] | None:
    """Read state after a restart; never changes the state."""
    validate_workspace_id(workspace_id)
    _validate_agent(agent)
    key = hashlib.sha256(f"{workspace_id}:{agent}".encode("utf-8")).hexdigest()[:24]
    path = _root(root) / "states" / f"{key}.json"
    if not path.is_file():
        return None
    state = _read(path)
    if state.get("workspace_id") != workspace_id or state.get("agent") != agent:
        raise ValueError("WORKSPACE_MISMATCH")
    return state


def read_events(root: Path, *, workspace_id: str, agent: str = "",
                session_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
    """Read durable events for recovery, filtered by workspace and identity."""
    validate_workspace_id(workspace_id)
    if agent:
        _validate_agent(agent)
    if not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be 1..1000")
    rows: list[dict[str, Any]] = []
    for path in sorted((_root(root) / "events").glob("*.json"), key=lambda item: item.stat().st_mtime):
        event = _read(path)
        if event.get("workspace_id") != workspace_id:
            continue
        if agent and event.get("agent") != agent:
            continue
        if session_id and event.get("session_id") != session_id:
            continue
        rows.append(event)
    return rows[-limit:]


def acknowledge(root: Path, *, event_id: str, status: str,
                evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist a runner receipt without replaying the associated event."""
    _validate_event(event_id)
    if status not in STATUS:
        raise ValueError("status must be READY, UNAVAILABLE or UNKNOWN")
    event_path = _root(root) / "events" / f"{event_id}.json"
    if not event_path.is_file():
        raise ValueError("event not found")
    source = _read(event_path)
    receipt = {"schema_version": 1, "type": "runner_receipt", "event_id": event_id,
               "workspace_id": source["workspace_id"], "agent": source["agent"],
               "session_id": source["session_id"], "status": status,
               "evidence": dict(evidence or {}), "updated_at": _now()}
    path = _root(root) / "receipts" / f"{event_id}.json"
    if path.is_file():
        previous = _read(path)
        stable = ("event_id", "workspace_id", "agent", "session_id", "status", "evidence")
        if _fingerprint({k: previous.get(k) for k in stable}) != _fingerprint({k: receipt.get(k) for k in stable}):
            raise ValueError("RECEIPT_CONFLICT")
        return {"status": "DUPLICATE", "receipt": previous, "execution_started": False}
    _atomic(path, receipt)
    return {"status": "PERSISTED", "receipt": receipt, "execution_started": False}


def recover(root: Path, *, workspace_id: str, agent: str, session_id: str = "") -> dict[str, Any]:
    """Return all durable evidence needed by a restarted Supervisor/Runner."""
    state = read_state(root, workspace_id=workspace_id, agent=agent)
    if session_id and state and state.get("session_id") != session_id:
        state = {"status": "UNKNOWN", "reason": "SESSION_CHANGED", "previous": state}
    return {"workspace_id": workspace_id, "agent": agent, "state": state,
            "events": read_events(root, workspace_id=workspace_id, agent=agent,
                                   session_id=session_id, limit=200),
            "recovered": True}
