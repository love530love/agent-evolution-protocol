"""Idempotently project explicit member joins into the shared JSON journal.

The membership database is authoritative. This is a replayable projection,
not a desktop notification, identity check, read receipt, or task ACK.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from . import conversation_journal, workspace_members


def _event(row: dict, room: str) -> dict:
    # The source sequence is allocated transactionally with the member record.
    # A deterministic name makes replay after a crash safe across processes.
    instant = datetime.fromisoformat(row["joined_at"].replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("JOIN_TIMESTAMP_HAS_NO_TIMEZONE")
    stamp = instant.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    key = json.dumps([row["workspace_id"], row["seq"], room], ensure_ascii=True,
                     separators=(",", ":")).encode("utf-8")
    event_id = stamp + "-" + hashlib.sha256(key).hexdigest()[:12]
    return {
        "id": event_id, "recorded_at": row["joined_at"],
        "room": room, "actor": "system", "source": "workspace-members",
        "kind": "member_joined", "text": f"成员 {row['agent']} 已提交工作区加入声明。",
        "session_id": row["session_id"], "cwd": "", "visibility": "workspace",
        "workspace_id": row["workspace_id"],
        "raw_hook": {"type": "workspace-member-join", "sequence": row["seq"],
                     "agent": row["agent"], "source": row["source"],
                     "joined_at": row["joined_at"]},
    }


def _publish(folder: Path, event: dict) -> bool:
    """Create a complete journal file once; never overwrite an existing file."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (event["id"] + ".json")
    temporary = folder / (event["id"] + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(event, handle, ensure_ascii=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # A hard link gives atomic create-if-absent without replacing a
            # different writer. Filesystems lacking this capability fail closed.
            os.link(temporary, target)
        except FileExistsError:
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
            except (UnicodeError, ValueError) as exc:
                raise ValueError("EXISTING_JOURNAL_EVENT_INVALID") from exc
            if existing != event:
                raise ValueError("JOURNAL_EVENT_ID_COLLISION")
            return False
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("JOIN_RECOVERY_BUDGET_EXCEEDED")


def project(coord_root: Path, *, workspace_id: str, after: int = 0,
            limit: int = 50, room: str = "agent-legion", timeout_seconds: float = 30,
            deadline: float | None = None) -> dict:
    if not isinstance(room, str) or not room.strip() or len(room) > 200:
        raise ValueError("invalid room")
    room = room.strip()
    _check_deadline(deadline)
    batch = workspace_members.notifications(coord_root, workspace_id=workspace_id,
                                            after=after, limit=limit, timeout_seconds=timeout_seconds)
    folder = conversation_journal.archive_root(coord_root) / "events"
    records = []
    for row in batch["events"]:
        _check_deadline(deadline)
        event = _event(row, room)
        created = _publish(folder, event)
        records.append({"sequence": row["seq"], "event_id": event["id"],
                        "created": created})
    return {
        "schema": "aep-member-join-journal-v1", "workspace_id": workspace_id,
        "room": room, "events": records, "next_cursor": batch["next_cursor"],
        "delivery": "NOT_ATTEMPTED", "agent_ack": False,
        "note": "Explicit join claims projected to a journal; no agent was woken or marked as having read them."
                " Consumers must also reconcile the authoritative member sequence for late projections.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("coordination"))
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--room", default="agent-legion")
    parser.add_argument("--after", type=int, default=0)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    print(json.dumps(project(args.root, workspace_id=args.workspace_id, after=args.after,
                             limit=args.limit, room=args.room), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
