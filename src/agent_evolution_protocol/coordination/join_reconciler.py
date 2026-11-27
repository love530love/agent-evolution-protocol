"""Replay member-join journal projections with a durable source cursor.

This worker only writes local journal/index metadata; it never wakes a client,
asserts a notification was read, or changes the authoritative membership DB.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from ..federation.locking import workspace_lock
from . import join_journal, workspace_members
from .workspace_identity import validate_workspace_id

SCHEMA = "aep-join-reconcile-cursor-v1"


def _key(workspace_id: str, room: str) -> tuple[str, str]:
    validate_workspace_id(workspace_id)
    if not isinstance(room, str) or not room.strip() or len(room) > 200:
        raise ValueError("invalid room")
    room = room.strip()
    digest = sha256(json.dumps([workspace_id, room], ensure_ascii=True,
                               separators=(",", ":")).encode("utf-8")).hexdigest()
    return room, digest


def _cursor_path(root: Path, digest: str) -> Path:
    return Path(root) / "join_reconciler" / (digest + ".json")


def _read_cursor(path: Path, workspace_id: str, room: str) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return {"schema": SCHEMA, "workspace_id": workspace_id, "room": room,
                "sequence": 0, "source_anchor": ""}
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError("CURSOR_UNREADABLE") from exc
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or value.get("workspace_id") != workspace_id or value.get("room") != room
            or isinstance(value.get("sequence"), bool)
            or not isinstance(value.get("sequence"), int) or value["sequence"] < 0
            or not isinstance(value.get("source_anchor"), str)
            or (value["sequence"] == 0 and value["source_anchor"])
            or (value["sequence"] > 0 and len(value["source_anchor"]) != 64)):
        raise ValueError("CURSOR_INVALID")
    return value


def _anchor(row: dict) -> str:
    return sha256(json.dumps(row, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _page(root: Path, workspace_id: str, *, after: int, limit: int,
          timeout_seconds: float = 30) -> list[dict] | None:
    source = Path(root) / workspace_members.DB_NAME
    try:
        source.stat()
    except FileNotFoundError:
        return None
    # An inaccessible or malformed source is an error, never an empty queue.
    db = sqlite3.connect(source.resolve(strict=True).as_uri() + "?mode=ro", uri=True, timeout=timeout_seconds)
    db.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in db.execute(
            "SELECT seq,workspace_id,agent,source,session_id,joined_at FROM join_events "
            "WHERE workspace_id=? AND seq>? ORDER BY seq LIMIT ?",
            (workspace_id, after, limit)).fetchall()]
    finally:
        db.close()


def _write_cursor(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=True, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def status(root: Path, *, workspace_id: str, room: str = "agent-legion") -> dict:
    """Read the saved source cursor; does not create directories or claim health."""
    room, digest = _key(workspace_id, room)
    cursor = _read_cursor(_cursor_path(root, digest), workspace_id, room)
    return {"schema": "aep-join-reconcile-status-v1", "workspace_id": workspace_id,
            "room": room, "sequence": cursor["sequence"],
            "source_checked": False, "notification_delivered": False, "agent_ack": False}


def reconcile(root: Path, *, workspace_id: str, room: str = "agent-legion",
              limit: int = 50, lock_timeout_seconds: float = 10,
              timeout_seconds: float = 30, deadline: float | None = None) -> dict:
    """Process one bounded source page; replay safely after an interrupted call."""
    room, digest = _key(workspace_id, room)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    cursor_path = _cursor_path(root, digest)
    join_journal._check_deadline(deadline)
    with workspace_lock(cursor_path.with_suffix(".lock"), timeout=lock_timeout_seconds):
        join_journal._check_deadline(deadline)
        cursor = _read_cursor(cursor_path, workspace_id, room)
        before = cursor["sequence"]
        if before:
            join_journal._check_deadline(deadline)
            previous = _page(root, workspace_id, after=before - 1, limit=1, timeout_seconds=timeout_seconds)
            if not previous or previous[0]["seq"] != before or _anchor(previous[0]) != cursor["source_anchor"]:
                raise ValueError("SOURCE_CURSOR_MISMATCH")
        join_journal._check_deadline(deadline)
        page = _page(root, workspace_id, after=before, limit=limit, timeout_seconds=timeout_seconds)
        common = {"schema": "aep-join-reconcile-v1", "workspace_id": workspace_id,
                  "room": room, "previous_sequence": before, "sequence": before,
                  "notification_delivered": False, "agent_ack": False}
        if page is None:
            return {**common, "status": "SOURCE_ABSENT", "projected": 0}
        if not page:
            return {**common, "status": "IDLE", "projected": 0}
        result = join_journal.project(root, workspace_id=workspace_id, room=room,
                                      after=before, limit=limit, timeout_seconds=timeout_seconds,
                                      deadline=deadline)
        if [row["sequence"] for row in result["events"]] != [row["seq"] for row in page]:
            raise ValueError("SOURCE_CHANGED_DURING_PROJECTION")
        join_journal._check_deadline(deadline)
        if _page(root, workspace_id, after=before, limit=limit, timeout_seconds=timeout_seconds) != page:
            raise ValueError("SOURCE_CHANGED_DURING_PROJECTION")
        last = page[-1]
        updated = {"schema": SCHEMA, "workspace_id": workspace_id, "room": room,
                   "sequence": last["seq"], "source_anchor": _anchor(last)}
        join_journal._check_deadline(deadline)
        _write_cursor(cursor_path, updated)
        return {**common, "status": "PROJECTED", "sequence": last["seq"],
                "projected": len(page), "events": result["events"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("reconcile", "status"))
    parser.add_argument("--root", type=Path, default=Path("coordination"))
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--room", default="agent-legion")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    result = (reconcile(args.root, workspace_id=args.workspace_id, room=args.room, limit=args.limit)
              if args.action == "reconcile" else status(args.root, workspace_id=args.workspace_id, room=args.room))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
