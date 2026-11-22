"""Explicit workspace membership and durable join notifications.

This independent registry is not an authentication, presence, wake or task ACK
mechanism. It records only explicit caller-supplied membership claims; old
unwatermarked messages and global presence are never inferred as membership.
The membership and its notification are committed in one SQLite transaction.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .presence import NAME
from .workspace_identity import validate_workspace_id

DB_NAME = "workspace_members.sqlite3"
SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
 workspace_id TEXT NOT NULL, agent TEXT NOT NULL, source TEXT NOT NULL,
 session_id TEXT NOT NULL, joined_at TEXT NOT NULL,
 PRIMARY KEY (workspace_id, agent)
);
CREATE TABLE IF NOT EXISTS join_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL,
 agent TEXT NOT NULL, source TEXT NOT NULL, session_id TEXT NOT NULL,
 joined_at TEXT NOT NULL, UNIQUE (workspace_id, agent)
);
CREATE INDEX IF NOT EXISTS join_events_workspace_seq
 ON join_events(workspace_id, seq);
"""


def _valid_agent(agent: str) -> str:
    if not isinstance(agent, str) or not NAME.fullmatch(agent):
        raise ValueError("invalid agent identity")
    return agent


def _text(value: str, field: str, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 500 or (required and not value.strip()):
        raise ValueError(f"invalid {field}")
    return value.strip()


def _write_connection(root: Path) -> sqlite3.Connection:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(root / DB_NAME), timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def _read_connection(root: Path) -> sqlite3.Connection | None:
    path = Path(root) / DB_NAME
    if not path.is_file():
        return None
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def join(root: Path, *, workspace_id: str, agent: str, source: str,
         session_id: str = "") -> dict:
    """Record one explicit joining identity, preserving the first attribution.

    Repeated or concurrent joins for the same workspace and agent yield one
    member and one notification. A client must drain notifications itself;
    writing this record does not wake or notify any desktop agent.
    """
    validate_workspace_id(workspace_id)
    _valid_agent(agent)
    source = _text(source, "source", required=True)
    session_id = _text(session_id, "session_id")
    db = _write_connection(root)
    try:
        db.execute("BEGIN IMMEDIATE")
        at = datetime.now(timezone.utc).isoformat()
        inserted = db.execute(
            "INSERT OR IGNORE INTO members(workspace_id,agent,source,session_id,joined_at) VALUES(?,?,?,?,?)",
            (workspace_id, agent, source, session_id, at),
        ).rowcount == 1
        if inserted:
            db.execute("INSERT INTO join_events(workspace_id,agent,source,session_id,joined_at) VALUES(?,?,?,?,?)",
                       (workspace_id, agent, source, session_id, at))
        row = dict(db.execute("SELECT * FROM members WHERE workspace_id=? AND agent=?",
                              (workspace_id, agent)).fetchone())
        event = dict(db.execute("SELECT seq FROM join_events WHERE workspace_id=? AND agent=?",
                                (workspace_id, agent)).fetchone())
        db.commit()
        return {"schema": "aep-workspace-member-join-v1", "created": inserted,
                "member": row, "event_seq": event["seq"],
                "delivery": "NOT_ATTEMPTED", "agent_ack": False,
                "note": "Caller-supplied identity claim; not authenticated or proof of presence."}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def members(root: Path, *, workspace_id: str) -> dict:
    validate_workspace_id(workspace_id)
    db = _read_connection(root)
    try:
        rows = [] if db is None else [dict(row) for row in db.execute(
            "SELECT workspace_id,agent,source,session_id,joined_at FROM members WHERE workspace_id=? ORDER BY joined_at,agent",
            (workspace_id,)).fetchall()]
    finally:
        if db is not None:
            db.close()
    return {"schema": "aep-workspace-members-v1", "workspace_id": workspace_id,
            "members": rows, "count": len(rows),
            "note": "Explicit recorded claims, not proof of authentication or current online status."}


def notifications(root: Path, *, workspace_id: str, after: int = 0,
                  limit: int = 50) -> dict:
    validate_workspace_id(workspace_id)
    if isinstance(after, bool) or not isinstance(after, int) or after < 0:
        raise ValueError("after must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    db = _read_connection(root)
    try:
        rows = [] if db is None else [dict(row) for row in db.execute(
            "SELECT seq,workspace_id,agent,source,session_id,joined_at FROM join_events "
            "WHERE workspace_id=? AND seq>? ORDER BY seq LIMIT ?",
            (workspace_id, after, limit)).fetchall()]
    finally:
        if db is not None:
            db.close()
    return {"schema": "aep-workspace-join-notifications-v1", "workspace_id": workspace_id,
            "events": rows, "count": len(rows),
            "next_cursor": rows[-1]["seq"] if rows else after,
            "note": "Durable join records only; reading does not send a message or acknowledge a task."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("join", "members", "notifications"))
    parser.add_argument("--root", type=Path, default=Path("coordination"))
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--agent", default="")
    parser.add_argument("--source", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--after", type=int, default=0)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    if args.action == "join":
        result = join(args.root, workspace_id=args.workspace_id, agent=args.agent,
                      source=args.source, session_id=args.session_id)
    elif args.action == "members":
        result = members(args.root, workspace_id=args.workspace_id)
    else:
        result = notifications(args.root, workspace_id=args.workspace_id,
                               after=args.after, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
