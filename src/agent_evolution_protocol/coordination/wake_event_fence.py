"""Durable, per-event at-most-once fence for the experimental wake router.

This does not replace the shared original-session lease used by UIA/CDP. It
prevents this router from re-invoking a transport for an event after an unclear
or completed attempt, including across process crashes and concurrent callers.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from ..federation.locking import workspace_lock


def _hash(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(data.encode("utf-8")).hexdigest()


def event_paths(root: Path, event_id: str) -> tuple[Path, Path]:
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id is required")
    digest = _hash(event_id)
    directory = Path(root) / "wake_router_events"
    return directory / (digest + ".lock"), directory / (digest + ".json")


def request_hash(*, event_id: str, agent: str, text: str, session: dict) -> str:
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    # The full text is never stored in the fence. The resolved workspace and
    # provider prevent an event being silently repurposed for another session.
    return _hash({"event_id": event_id, "agent": agent, "text": text,
                  "workspace": session.get("workspace", ""),
                  "provider": session.get("provider", ""),
                  "session_id": session.get("session_id", ""),
                  "conversation_id": session.get("conversation_id", "")})


def read(path: Path) -> dict | None:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeError):
        return {"state": "UNREADABLE"}
    if (not isinstance(row, dict) or row.get("schema") != "aep-wake-event-fence-v1"
            or row.get("state") not in {"IN_FLIGHT", "FINAL"}
            or not isinstance(row.get("event_id"), str) or not row["event_id"].strip()
            or not isinstance(row.get("request_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["request_sha256"])):
        return {"state": "UNREADABLE"}
    return row


def write(path: Path, *, event_id: str, request_sha256: str,
          state: str, status: str = "") -> None:
    row = {"schema": "aep-wake-event-fence-v1", "event_id": event_id,
           "request_sha256": request_sha256, "state": state, "status": status}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(row, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def acquire(root: Path, event_id: str):
    lock, _ = event_paths(root, event_id)
    return workspace_lock(lock)
