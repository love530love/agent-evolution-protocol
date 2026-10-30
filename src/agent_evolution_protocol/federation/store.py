"""Durable workspace storage: atomic JSON writes and a hash-chained event log.

The engine's only source of truth is the workspace directory on disk. Nothing
in this module keeps state in memory between calls, so a process crash, a
reboot, or a different agent process can resume from exactly what is stored.

Every event is a valid ``event-envelope-v1`` document: it carries the workspace
ID, a stable ``event_id``, an explicit ``parent_event_id``, and a SHA-256 link
to its predecessor, so a deleted, reordered or edited event is detectable rather
than silently accepted.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from ..coordination.workspace_identity import validate_workspace_id


EVENT_SCHEMA = "aep-event-envelope-v1"
#: Fields the event id is derived from; ``event_id`` and ``sha256`` are excluded
#: because both are computed from this block.
ID_FIELDS = ("schema", "seq", "at", "workspace_id", "kind", "actor", "payload",
             "prev_sha256", "parent_event_id")


def stamp(moment=None):
    return (moment or datetime.now(timezone.utc)).isoformat()


def canonical(payload):
    """Stable bytes for hashing: sorted keys, no insignificant whitespace."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(payload):
    return hashlib.sha256(canonical(payload)).hexdigest()


def atomic_write(path, payload):
    """Write JSON so that readers see either the old file or the new one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def read_json(path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def workspace_dir(root, workspace_id):
    return Path(root) / "workspaces" / validate_workspace_id(workspace_id)


class EventLog:
    """Append-only chain scoped to one workspace."""

    def __init__(self, root, workspace_id):
        self.workspace_id = validate_workspace_id(workspace_id)
        self.path = workspace_dir(root, workspace_id) / "events.jsonl"

    def events(self):
        if not self.path.is_file():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    def append(self, kind, *, actor, payload=None, moment=None):
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("event kind is required")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("event actor is required")
        rows = self.events()
        previous = rows[-1]["sha256"] if rows else ""
        base = {"schema": EVENT_SCHEMA, "seq": len(rows) + 1, "at": stamp(moment),
                "workspace_id": self.workspace_id, "kind": kind.strip(),
                "actor": actor.strip(), "payload": payload or {},
                "prev_sha256": previous,
                "parent_event_id": rows[-1].get("event_id") if rows else None}
        event = dict(base)
        event["event_id"] = "E" + digest(base)[:15]
        event["sha256"] = digest({key: event[key] for key in event if key != "sha256"})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return event

    def verify(self):
        """Return a report instead of raising, so callers can decide."""
        rows = self.events()
        previous = ""
        for index, event in enumerate(rows, start=1):
            if event.get("schema") != EVENT_SCHEMA:
                return {"valid": False, "seq": index, "reason": "unknown event schema"}
            if event.get("workspace_id") != self.workspace_id:
                return {"valid": False, "seq": index, "reason": "workspace watermark mismatch"}
            if event.get("seq") != index:
                return {"valid": False, "seq": index, "reason": "sequence gap or reorder"}
            if event.get("prev_sha256") != previous:
                return {"valid": False, "seq": index, "reason": "broken hash chain"}
            expected = digest({key: event[key] for key in event if key != "sha256"})
            if event.get("sha256") != expected:
                return {"valid": False, "seq": index, "reason": "event content changed"}
            base = {key: event.get(key) for key in ID_FIELDS}
            if event.get("event_id") != "E" + digest(base)[:15]:
                return {"valid": False, "seq": index, "reason": "event id does not match content"}
            if index > 1 and event.get("parent_event_id") != rows[index - 2].get("event_id"):
                return {"valid": False, "seq": index, "reason": "parent event link is wrong"}
            previous = event["sha256"]
        return {"valid": True, "events": len(rows), "head": previous}
