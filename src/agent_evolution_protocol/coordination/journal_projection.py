"""Rebuildable SQLite projection of an AEP conversation journal.

The per-event JSON journal remains authoritative. Rebuild is explicit and
transactional: no cursor advances before an entire source scan commits. A
missing journal leaves any previous index untouched. Unbound events never
appear in workspace-filtered queries; malformed events are reported separately.
No operation here sends a message, wakes an agent, or marks an ACK.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from .conversation_journal import SAFE_EVENT, archive_root
from .workspace_identity import validate_workspace_id

SCHEMA = "aep-journal-projection-v1"
_FIELDS = ("recorded_at", "room", "actor", "source", "kind", "text")


def _source(root: Path) -> Path:
    return archive_root(Path(root).resolve()) / "events"


def _event(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        row = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("INVALID_JSON") from exc
    if not isinstance(row, dict) or row.get("id") != path.stem:
        raise ValueError("EVENT_ID_MISMATCH")
    if any(not isinstance(row.get(k), str) for k in _FIELDS):
        raise ValueError("INVALID_FIELDS")
    try:
        stamp = datetime.fromisoformat(row["recorded_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("no timezone")
    except ValueError as exc:
        raise ValueError("INVALID_TIMESTAMP") from exc
    workspace = row.get("workspace_id")
    if workspace is not None:
        try:
            validate_workspace_id(workspace)
        except ValueError as exc:
            raise ValueError("INVALID_WORKSPACE_ID") from exc
    row["workspace_id"] = workspace
    return row, hashlib.sha256(raw).hexdigest()


def _prepare(db: sqlite3.Connection, source: Path) -> None:
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables:
        if "projection_meta" not in tables:
            raise ValueError("DATABASE_NOT_JOURNAL_PROJECTION")
        existing = dict(db.execute("SELECT key, value FROM projection_meta"))
        if existing.get("schema") != SCHEMA or existing.get("source") != str(source):
            raise ValueError("PROJECTION_SOURCE_OR_SCHEMA_MISMATCH")
    else:
        db.execute("CREATE TABLE projection_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, workspace_id TEXT, recorded_at TEXT NOT NULL, room TEXT NOT NULL, actor TEXT NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, session_id TEXT NOT NULL, source_path TEXT NOT NULL, sha256 TEXT NOT NULL)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_projected_workspace_event ON events(workspace_id, event_id)")
    db.execute("CREATE TABLE IF NOT EXISTS projection_errors (source_path TEXT PRIMARY KEY, reason TEXT NOT NULL)")
    db.executemany("INSERT OR REPLACE INTO projection_meta(key,value) VALUES (?,?)", (("schema", SCHEMA), ("source", str(source))))


def rebuild(coord_root: Path, db_path: Path) -> dict:
    source = _source(coord_root)
    db_path = Path(db_path).resolve()
    if not source.is_dir():
        return {"schema": SCHEMA, "status": "SOURCE_ABSENT", "indexed": 0, "errors": [], "database_unchanged": True}
    if db_path == source or source in db_path.parents:
        raise ValueError("DATABASE_INSIDE_JOURNAL")
    # Unlike Path.glob on some Python versions, scandir propagates directory
    # errors. Never replace a good snapshot with an apparent empty directory
    # merely because the source cannot be listed.
    with os.scandir(source) as directory:
        entries = sorted((Path(entry.path), entry.is_file(follow_symlinks=False))
                         for entry in directory if entry.name.endswith(".json"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    errors: list[dict] = []
    indexed = unbound = 0
    try:
        db.execute("BEGIN IMMEDIATE")
        _prepare(db, source)
        # Rebuild inside one SQLite transaction: readers see either the old
        # complete projection or the new complete projection, never a half scan.
        db.execute("DELETE FROM events")
        db.execute("DELETE FROM projection_errors")
        for path, is_file in entries:
            if not is_file:
                errors.append({"source_path": str(path), "reason": "NOT_REGULAR_FILE"})
                continue
            if not SAFE_EVENT.fullmatch(path.stem):
                errors.append({"source_path": str(path), "reason": "INVALID_FILENAME"})
                continue
            try:
                row, digest = _event(path)
            except (OSError, ValueError) as exc:
                reason = str(exc) if isinstance(exc, ValueError) else "SOURCE_UNREADABLE"
                errors.append({"source_path": str(path), "reason": reason})
                continue
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                row["id"], row["workspace_id"], row["recorded_at"], row["room"],
                row["actor"], row["source"], row["kind"], row["text"],
                row.get("session_id") if isinstance(row.get("session_id"), str) else "",
                str(path), digest))
            indexed += 1
            unbound += row["workspace_id"] is None
        db.executemany("INSERT INTO projection_errors(source_path,reason) VALUES (?,?)",
                       ((item["source_path"], item["reason"]) for item in errors))
        db.execute("INSERT OR REPLACE INTO projection_meta(key,value) VALUES ('indexed_at',?)",
                   (datetime.now(timezone.utc).isoformat(),))
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()
    return {"schema": SCHEMA, "status": "REBUILT", "indexed": indexed,
            "unbound": unbound, "errors": errors, "database": str(db_path),
            "note": "A rebuildable index, not proof of delivery, reading or acceptance."}


def search(db_path: Path, *, workspace_id: str = "", unbound: bool = False,
           contains: str = "", room: str = "", after: str = "", limit: int = 50) -> dict:
    if unbound and workspace_id:
        raise ValueError("UNBOUND_WORKSPACE_CONFLICT")
    if not unbound:
        validate_workspace_id(workspace_id)
    if after and not SAFE_EVENT.fullmatch(after):
        raise ValueError("INVALID_CURSOR")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    path = Path(db_path).resolve()
    if not path.is_file():
        return {"schema": SCHEMA, "status": "INDEX_ABSENT", "events": [], "next_cursor": after}
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        meta = dict(db.execute("SELECT key,value FROM projection_meta"))
        if meta.get("schema") != SCHEMA:
            raise ValueError("DATABASE_NOT_JOURNAL_PROJECTION")
        scope = "workspace_id IS NULL" if unbound else "workspace_id=?"
        params = ([] if unbound else [workspace_id]) + [after, room, room, contains, limit]
        rows = db.execute("SELECT * FROM events WHERE " + scope +
                          " AND event_id>? AND (?='' OR room=?) AND instr(lower(text),lower(?))>0 ORDER BY event_id LIMIT ?",
                          params).fetchall()
        events = [dict(row) for row in rows]
        issues = db.execute("SELECT COUNT(*) FROM projection_errors").fetchone()[0]
    finally:
        db.close()
    return {"schema": SCHEMA, "status": "OK", "workspace_id": workspace_id or None,
            "scope": "unbound" if unbound else "workspace",
            "indexed_at": meta.get("indexed_at", ""), "source": meta.get("source", ""),
            "events": events, "next_cursor": events[-1]["event_id"] if events else after,
            "projection_error_count": issues,
            "note": "Results are a snapshot; JSON source is authoritative. Visibility is not an ACK."}


def status(db_path: Path, *, error_limit: int = 50) -> dict:
    if isinstance(error_limit, bool) or not isinstance(error_limit, int) or not 1 <= error_limit <= 500:
        raise ValueError("error_limit must be between 1 and 500")
    path = Path(db_path).resolve()
    if not path.is_file():
        return {"schema": SCHEMA, "status": "INDEX_ABSENT", "errors": []}
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        meta = dict(db.execute("SELECT key,value FROM projection_meta"))
        if meta.get("schema") != SCHEMA:
            raise ValueError("DATABASE_NOT_JOURNAL_PROJECTION")
        total, unbound = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(CASE WHEN workspace_id IS NULL THEN 1 ELSE 0 END), 0) FROM events").fetchone()
        errors = [dict(row) for row in db.execute(
            "SELECT source_path,reason FROM projection_errors ORDER BY source_path LIMIT ?", (error_limit,))]
        error_count = db.execute("SELECT COUNT(*) FROM projection_errors").fetchone()[0]
    finally:
        db.close()
    return {"schema": SCHEMA, "status": "OK", "indexed_at": meta.get("indexed_at", ""),
            "source": meta.get("source", ""), "indexed": total, "unbound": unbound,
            "error_count": error_count, "errors": errors,
            "note": "Snapshot only; status does not check whether source files changed after indexing."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("rebuild", "search", "status"))
    parser.add_argument("--coord-root", required=True, type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--workspace-id", default="")
    parser.add_argument("--unbound", action="store_true", help="inspect unwatermarked events separately")
    parser.add_argument("--room", default="")
    parser.add_argument("--contains", default="")
    parser.add_argument("--after", default="")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    path = args.db or args.coord_root / "journal-index.sqlite3"
    if args.action == "rebuild":
        result = rebuild(args.coord_root, path)
    elif args.action == "status":
        result = status(path, error_limit=args.limit)
    else:
        result = search(path, workspace_id=args.workspace_id, unbound=args.unbound, contains=args.contains,
                        room=args.room, after=args.after, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
