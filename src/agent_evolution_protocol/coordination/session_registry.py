"""Persistent Agent session registry and exact target resolution.

The desktop layer can discover several windows with the same title.  This
registry gives every connection a stable identity (agent + workspace + session)
and makes delivery state explicit.  A caller must resolve a unique target
before sending; ambiguous or stale targets are refused instead of guessed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

STATES = {"discovered", "registered", "delivered", "acknowledged", "offline", "failed"}


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


def register(root: Path, *, agent: str, workspace: str, provider: str,
             session_id: str, window_id: str = "", conversation_id: str = "",
             capabilities: list[str] | None = None) -> dict:
    if not all(isinstance(x, str) and x.strip() for x in (agent, workspace, provider, session_id)):
        raise ValueError("agent, workspace, provider and session_id are required")
    key = f"{agent}__{session_id}".replace("/", "_").replace("\\", "_")
    old = _read(_dir(root) / f"{key}.json")
    row = {**old, "agent": agent, "workspace": str(Path(workspace).resolve()),
           "provider": provider, "session_id": session_id, "window_id": window_id,
           "conversation_id": conversation_id, "capabilities": capabilities or old.get("capabilities", []),
           "state": "registered", "updated_at": _now(), "registered_at": old.get("registered_at", _now())}
    _write(_dir(root) / f"{key}.json", row)
    return row


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
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
            conversation_id: str = "", window_id: str = "") -> dict:
    rows = list_sessions(root, agent=agent, workspace=workspace)
    if session_id: rows = [r for r in rows if r.get("session_id") == session_id]
    if conversation_id: rows = [r for r in rows if r.get("conversation_id") == conversation_id]
    if window_id: rows = [r for r in rows if r.get("window_id") == window_id]
    live = [r for r in rows if r.get("state") not in {"offline", "failed"}]
    if len(live) != 1:
        reason = "NO_SESSION" if not live else "AMBIGUOUS_SESSION"
        return {"ok": False, "reason": reason, "candidates": live}
    return {"ok": True, "session": live[0]}


def transition(root: Path, *, agent: str, session_id: str, state: str,
               event_id: str = "", detail: str = "") -> dict:
    if state not in STATES: raise ValueError(f"invalid state: {state}")
    rows = list_sessions(root, agent=agent, session_id=session_id)
    if len(rows) != 1: raise ValueError("session not uniquely registered")
    row = dict(rows[0]); row["state"] = state; row["updated_at"] = _now()
    if event_id: row["last_event_id"] = event_id
    if detail: row["last_detail"] = detail
    key = f"{agent}__{session_id}".replace("/", "_").replace("\\", "_")
    _write(_dir(root) / f"{key}.json", row)
    return row
