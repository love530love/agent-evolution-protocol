"""Read a Codex Desktop turn result from its own rollout, without trusting the UI.

UIA proves a message is visible; it does not prove which thread received it.  The
desktop client already keeps that fact next to the conversation: ``state_5``
maps a thread to its rollout JSONL, and the rollout's ``task_complete`` event
carries the agent's final message for one turn.  Reading that file is a second,
independent source for ``TASK_RESULT_READBACK``.

Everything here is read-only.  A missing database, locked WAL, absent thread,
or malformed line is reported as a status, never repaired or created.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from agent_evolution_protocol.isotime import parse_iso_or_none

DEFAULT_STATE_DB = Path.home() / ".codex" / "state_5.sqlite"
SCHEMA = "aep-codex-rollout-v1"
SEEN = "seen"
ABSENT = "absent"
NO_THREAD = "no_thread"
UNREADABLE = "unreadable"
_EXTENDED = re.compile(r"^\\\\\?\\")


def _path(value: str) -> Path:
    return Path(_EXTENDED.sub("", str(value or "")))


def _connect(state_db: Path) -> sqlite3.Connection:
    target = Path(state_db)
    if not target.is_file():
        raise FileNotFoundError(str(target))
    con = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def find_threads(state_db: Path = DEFAULT_STATE_DB, *,
                 title_marker: str = "", cwd_marker: str = "",
                 limit: int = 20) -> list[dict[str, Any]]:
    """Threads newest first, optionally filtered by a title/cwd substring."""
    clauses: list[str] = []
    values: list[Any] = []
    if title_marker:
        clauses.append("title LIKE ?")
        values.append(f"%{title_marker}%")
    if cwd_marker:
        clauses.append("cwd LIKE ?")
        values.append(f"%{cwd_marker}%")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    values.append(max(1, min(int(limit), 200)))
    con = _connect(state_db)
    try:
        rows = con.execute(
            "SELECT id, title, cwd, updated_at, rollout_path FROM threads"
            + where + " ORDER BY updated_at DESC LIMIT ?", values).fetchall()
    finally:
        con.close()
    return [{"thread_id": row["id"], "title": row["title"],
             "cwd": row["cwd"], "updated_at": row["updated_at"],
             "rollout_path": str(_path(row["rollout_path"]))} for row in rows]


def read_result(thread: dict[str, Any], marker: str, *,
                since: datetime | str | None = None) -> dict[str, Any]:
    """The newest ``task_complete`` final message containing *marker*."""
    if not isinstance(thread, dict):
        raise ValueError("thread must be an object")
    needle = re.sub(r"\s+", "", str(marker or ""))
    if not needle:
        raise ValueError("marker must not be empty")
    rollout = _path(thread.get("rollout_path", ""))
    base = {
        "schema": SCHEMA,
        "thread_id": str(thread.get("thread_id", "")),
        "title": str(thread.get("title", "")),
        "cwd": str(thread.get("cwd", "")),
        "rollout_path": str(rollout),
        "marker": str(marker),
        "status": ABSENT,
        "task_result_readback": False,
        "turn_id": "",
        "completed_at": "",
        "message": "",
        "rollout_sha256": "",
    }
    if not rollout.is_file():
        return {**base, "status": UNREADABLE, "reason": "rollout_missing"}
    since_at = None
    if since is not None:
        parsed = parse_iso_or_none(since) if isinstance(since, str) else since
        if parsed is None:
            return {**base, "status": UNREADABLE, "reason": "since_invalid"}
        since_at = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    try:
        data = rollout.read_bytes()
    except OSError:
        return {**base, "status": UNREADABLE, "reason": "rollout_unreadable"}
    digest = sha256(data).hexdigest()
    found: dict[str, Any] | None = None
    for raw in data.decode("utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload") if isinstance(event, dict) else None
        if not isinstance(payload, dict) or payload.get("type") != "task_complete":
            continue
        message = str(payload.get("last_agent_message") or "")
        if needle not in re.sub(r"\s+", "", message):
            continue
        timestamp = str(event.get("timestamp", ""))
        observed = parse_iso_or_none(timestamp)
        if since_at is not None and observed is not None:
            if (observed if observed.tzinfo else observed.replace(tzinfo=timezone.utc)) < since_at:
                continue
        found = {
            "turn_id": str(payload.get("turn_id", "")),
            "completed_at": timestamp,
            "message": message,
        }
    if found is None:
        return {**base, "rollout_sha256": digest}
    return {**base, **found, "status": SEEN, "task_result_readback": True,
            "rollout_sha256": digest}


def _content_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for row in content:
        if isinstance(row, dict) and isinstance(row.get("text"), str):
            parts.append(row["text"])
    return "\n".join(parts)


def read_submission(thread: dict[str, Any], marker: str, *,
                    since: datetime | str | None = None) -> dict[str, Any]:
    """The newest user message containing *marker* in the thread rollout.

    This is delivery evidence, not completion evidence: only a user message is
    accepted, never an assistant message or tool output.  It lets a caller
    confirm an uncertain UI send without resending it.
    """
    if not isinstance(thread, dict):
        raise ValueError("thread must be an object")
    needle = re.sub(r"\s+", "", str(marker or ""))
    if not needle:
        raise ValueError("marker must not be empty")
    rollout = _path(thread.get("rollout_path", ""))
    base = {
        "schema": SCHEMA,
        "thread_id": str(thread.get("thread_id", "")),
        "title": str(thread.get("title", "")),
        "cwd": str(thread.get("cwd", "")),
        "rollout_path": str(rollout),
        "marker": str(marker),
        "status": ABSENT,
        "message_seen": False,
        "message_id": "",
        "turn_id": "",
        "submitted_at": "",
        "rollout_sha256": "",
    }
    if not rollout.is_file():
        return {**base, "status": UNREADABLE, "reason": "rollout_missing"}
    since_at = None
    if since is not None:
        parsed = parse_iso_or_none(since) if isinstance(since, str) else since
        if parsed is None:
            return {**base, "status": UNREADABLE, "reason": "since_invalid"}
        since_at = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    try:
        data = rollout.read_bytes()
    except OSError:
        return {**base, "status": UNREADABLE, "reason": "rollout_unreadable"}
    digest = sha256(data).hexdigest()
    found: dict[str, Any] | None = None
    for raw in data.decode("utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if event.get("type") == "event_msg" and payload.get("type") == "item_completed":
            item = payload.get("item")
            if not isinstance(item, dict) or item.get("type") != "UserMessage":
                continue
            turn_id = str(payload.get("turn_id", ""))
        elif (event.get("type") == "response_item"
              and payload.get("type") == "message"
              and payload.get("role") == "user"):
            item = payload
            turn_id = str(item.get("turn_id", ""))
        else:
            continue
        text = _content_text(item.get("content"))
        if needle not in re.sub(r"\s+", "", text):
            continue
        timestamp = str(event.get("timestamp", ""))
        observed = parse_iso_or_none(timestamp)
        if since_at is not None and observed is not None:
            if (observed if observed.tzinfo else observed.replace(tzinfo=timezone.utc)) < since_at:
                continue
        found = {
            "message_id": str(item.get("id", "")),
            "turn_id": turn_id,
            "submitted_at": timestamp,
        }
    if found is None:
        return {**base, "rollout_sha256": digest}
    return {**base, **found, "status": SEEN, "message_seen": True,
            "rollout_sha256": digest}


def submission_for_thread(state_db: Path, thread_id: str, marker: str, *,
                          since: datetime | str | None = None) -> dict[str, Any]:
    con = _connect(state_db)
    try:
        row = con.execute(
            "SELECT id, title, cwd, updated_at, rollout_path FROM threads WHERE id = ?",
            (str(thread_id),)).fetchone()
    finally:
        con.close()
    if row is None:
        return {"schema": SCHEMA, "thread_id": str(thread_id), "status": NO_THREAD,
                "message_seen": False, "marker": str(marker),
                "reason": "thread_not_found"}
    thread = {"thread_id": row["id"], "title": row["title"], "cwd": row["cwd"],
              "updated_at": row["updated_at"], "rollout_path": str(_path(row["rollout_path"]))}
    return read_submission(thread, marker, since=since)


def result_for_thread(state_db: Path, thread_id: str, marker: str, *,
                      since: datetime | str | None = None) -> dict[str, Any]:
    con = _connect(state_db)
    try:
        row = con.execute(
            "SELECT id, title, cwd, updated_at, rollout_path FROM threads WHERE id = ?",
            (str(thread_id),)).fetchone()
    finally:
        con.close()
    if row is None:
        return {"schema": SCHEMA, "thread_id": str(thread_id), "status": NO_THREAD,
                "task_result_readback": False, "marker": str(marker),
                "reason": "thread_not_found"}
    thread = {"thread_id": row["id"], "title": row["title"], "cwd": row["cwd"],
              "updated_at": row["updated_at"], "rollout_path": str(_path(row["rollout_path"]))}
    return read_result(thread, marker, since=since)


def find_result(state_db: Path, marker: str, *, title_marker: str = "",
                cwd_marker: str = "", since: datetime | str | None = None,
                limit: int = 20) -> dict[str, Any]:
    """Search newest threads for a marker-bearing completed final message."""
    threads = find_threads(state_db, title_marker=title_marker,
                           cwd_marker=cwd_marker, limit=limit)
    if not threads:
        return {"schema": SCHEMA, "status": NO_THREAD,
                "task_result_readback": False, "marker": str(marker),
                "reason": "thread_not_found", "threads": 0}
    checked: list[dict[str, Any]] = []
    for thread in threads:
        result = read_result(thread, marker, since=since)
        checked.append({"thread_id": result["thread_id"], "status": result["status"]})
        if result["status"] == SEEN:
            return {**result, "threads": len(threads), "checked": checked}
    if checked and all(row["status"] == UNREADABLE for row in checked):
        return {"schema": SCHEMA, "status": UNREADABLE,
                "task_result_readback": False, "marker": str(marker),
                "threads": len(threads), "checked": checked,
                "reason": "all_rollouts_unreadable"}
    return {"schema": SCHEMA, "status": ABSENT, "task_result_readback": False,
            "marker": str(marker), "threads": len(threads), "checked": checked}
