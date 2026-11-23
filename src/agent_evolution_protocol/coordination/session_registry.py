"""Persistent Agent session registry and exact target resolution.

The desktop layer can discover several windows with the same title.  This
registry gives every connection a stable identity (agent + workspace + session)
and makes delivery state explicit.  A caller must resolve a unique target
before sending; ambiguous or stale targets are refused instead of guessed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from uuid import uuid4

from ..federation.locking import workspace_lock

STATES = {"discovered", "registered", "delivered", "acknowledged", "offline", "failed"}
DEFAULT_REGISTRATION_MAX_AGE_SECONDS = 900.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{uuid4().hex}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _dir(root: Path) -> Path:
    path = Path(root) / "clients" / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key(agent: str, session_id: str) -> str:
    return f"{agent}__{session_id}".replace("/", "_").replace("\\", "_")


def _workspace_key(value: str) -> str:
    return os.path.normcase(str(Path(value).resolve()))


def register(root: Path, *, agent: str, workspace: str, provider: str,
             session_id: str, window_id: str = "", conversation_id: str = "",
             capabilities: list[str] | None = None) -> dict:
    if not all(isinstance(x, str) and x.strip() for x in (agent, workspace, provider, session_id)):
        raise ValueError("agent, workspace, provider and session_id are required")
    directory = _dir(root)
    path = directory / f"{_key(agent, session_id)}.json"
    with workspace_lock(directory / ".registry.lock"):
        old = _read(path)
        if path.exists():
            if not isinstance(old, dict) or not all(isinstance(old.get(k), str) and old[k].strip()
                                                    for k in ("agent", "workspace", "provider", "session_id")):
                raise ValueError("SESSION_RECORD_UNREADABLE")
            if (old["agent"] != agent or old["session_id"] != session_id
                    or old["provider"] != provider
                    or _workspace_key(old["workspace"]) != _workspace_key(workspace)):
                raise ValueError("SESSION_IDENTITY_CONFLICT")
        registered_now = _now()
        row = {**old, "agent": agent, "workspace": str(Path(workspace).resolve()),
               "provider": provider, "session_id": session_id, "window_id": window_id,
               "conversation_id": conversation_id, "capabilities": capabilities or old.get("capabilities", []),
               "state": "registered", "updated_at": registered_now,
               "registration_refreshed_at": registered_now,
               "registered_at": old.get("registered_at", registered_now)}
        _write(path, row)
        return row


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def list_sessions(root: Path, *, agent: str = "", workspace: str = "", session_id: str = "") -> list[dict]:
    rows = [_read(p) for p in _dir(root).glob("*.json")]
    rows = [r for r in rows if r.get("session_id")]
    if agent: rows = [r for r in rows if r.get("agent", "").casefold() == agent.casefold()]
    if session_id: rows = [r for r in rows if r.get("session_id") == session_id]
    if workspace:
        wanted = str(Path(workspace).resolve()).casefold()
        rows = [r for r in rows if str(r.get("workspace", "")).casefold() == wanted]
    return sorted(rows, key=lambda r: r.get("updated_at", ""), reverse=True)


def resolve(root: Path, *, agent: str, workspace: str = "", session_id: str = "",
            conversation_id: str = "", window_id: str = "",
            max_age_seconds: float = DEFAULT_REGISTRATION_MAX_AGE_SECONDS,
            now: datetime | None = None) -> dict:
    """Resolve one identity with a recent registration, not a live observation.

    Timestamps from state transitions never renew the registration. Old rows
    lacking registration_refreshed_at must be explicitly re-registered.
    """
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)) \
            or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be a finite positive number")
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    rows = list_sessions(root, agent=agent, workspace=workspace)
    if session_id: rows = [r for r in rows if r.get("session_id") == session_id]
    if conversation_id: rows = [r for r in rows if r.get("conversation_id") == conversation_id]
    if window_id: rows = [r for r in rows if r.get("window_id") == window_id]
    live = [r for r in rows if r.get("state") not in {"offline", "failed"}]
    if len(live) != 1:
        reason = "NO_SESSION" if not live else "AMBIGUOUS_SESSION"
        return {"ok": False, "reason": reason, "candidates": live}
    row = live[0]
    stamp = row.get("registration_refreshed_at")
    try:
        if not isinstance(stamp, str) or not stamp.strip():
            raise ValueError("registration timestamp missing")
        registered = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if registered.utcoffset() is None:
            raise ValueError("registration timestamp has no timezone")
        age = (now - registered).total_seconds()
    except (ValueError, TypeError, OverflowError):
        return {"ok": False, "reason": "SESSION_REGISTRATION_TIME_UNKNOWN", "candidates": live}
    if age < 0:
        return {"ok": False, "reason": "SESSION_REGISTRATION_IN_FUTURE", "candidates": live}
    if age > max_age_seconds:
        return {"ok": False, "reason": "SESSION_REGISTRATION_STALE", "candidates": live,
                "registration_age_seconds": age}
    return {"ok": True, "session": row, "registration_age_seconds": age,
            "session_observation_verified": False}


def transition(root: Path, *, agent: str, session_id: str, state: str,
               event_id: str = "", detail: str = "") -> dict:
    if state not in STATES: raise ValueError(f"invalid state: {state}")
    directory = _dir(root)
    with workspace_lock(directory / ".registry.lock"):
        rows = list_sessions(root, agent=agent, session_id=session_id)
        if len(rows) != 1: raise ValueError("session not uniquely registered")
        row = dict(rows[0]); row["state"] = state; row["updated_at"] = _now()
        if event_id: row["last_event_id"] = event_id
        if detail: row["last_detail"] = detail
        _write(directory / f"{_key(row['agent'], row['session_id'])}.json", row)
        return row
