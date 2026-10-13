"""Event-driven team presence: evidence, transitions, unread handoff, no polling."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from . import conversation_journal

NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
STATES = {"online", "offline"}


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def _lock(root: Path):
    path = root / "presence" / "presence.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def effective(row: dict, at: datetime | None = None) -> str:
    if row.get("state") != "online":
        return row.get("state", "unknown")
    try:
        expiry = datetime.fromisoformat(row["expires_at"])
        return "online" if expiry > (at or _utc()) else "stale"
    except (KeyError, TypeError, ValueError):
        return "stale"


def unread(root: Path, agent: str, *, limit: int = 30) -> dict:
    cursor = conversation_journal.cursor_for(root, agent)
    folder = conversation_journal.archive_root(root) / "events"
    paths = [p for p in sorted(folder.glob("*.json"))
             if conversation_journal.SAFE_EVENT.fullmatch(p.stem) and p.stem > cursor]
    events = [_read(path) for path in paths[-limit:]]
    return {"count": len(paths), "cursor": cursor, "latest": paths[-1].stem if paths else "",
            "items": [{"id": e.get("id"), "actor": e.get("actor"), "kind": e.get("kind"),
                       "text": str(e.get("text", ""))[:600]} for e in events]}


def _active_assignments(root: Path, agent: str) -> list[str]:
    result = []
    for path in sorted((root / "campaign" / "missions").glob("T*.json")):
        state = _read(path)
        for row in state.get("assignments", []):
            if row.get("agent") == agent and row.get("status") in {"awaiting_ack", "active"}:
                result.append(str(row.get("id", path.stem)))
    return result


def _notice(root: Path, *, agent: str, transition: str, state: str,
            previous: str, source: str, at: datetime) -> dict:
    pending = unread(root, agent)
    assignments = _active_assignments(root, agent)
    action = ("handoff_unread_then_confirm_assignments" if state == "online" else
              "verify_or_wake_then_reassign_if_deadline_risk" if state == "stale" else
              "reassign_active_work_and_wake_only_if_needed")
    event = {"id": at.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8],
             "at": at.isoformat(), "agent": agent, "transition": transition,
             "previous": previous, "state": state, "source": source,
             "unread_count": pending["count"], "unread_latest": pending["latest"],
             "active_assignments": assignments, "recommendation": action,
             "note": "Presence is not task ACK; stale means evidence expired, not proven offline."}
    _write(root / "presence" / "notices" / (event["id"] + ".json"), event)
    if state == "online" and pending["count"]:
        _write(root / "presence" / "digests" / (event["id"] + ".json"),
               {"agent": agent, "notice_id": event["id"], "unread": pending})
    conversation_journal.append_event(
        root, room="agent-legion", actor="presence", source="presence-roster", kind="status",
        text=(f"{agent}: {previous} → {state} ({transition}); online strength changed. "
              f"Unread for {agent}: {pending['count']}; active assignments: {len(assignments)}. "
              f"Suggested action: {action}. Presence does not replace ACK."),
    )
    return event


def signal(root: Path, *, agent: str, state: str, source: str,
           session_id: str = "", ttl_minutes: int = 30, reason: str = "") -> dict:
    if not NAME.fullmatch(agent) or state not in STATES or not source.strip():
        raise ValueError("valid agent, online/offline state and source required")
    if not 1 <= ttl_minutes <= 120:
        raise ValueError("TTL must be 1..120 minutes")
    root = Path(root)
    with _lock(root):
        path = root / "presence" / "agents" / f"{agent}.json"
        previous_row = _read(path)
        at = _utc()
        previous = effective(previous_row, at)
        row = {"agent": agent, "state": state, "source": source,
               "session_id": session_id, "reason": reason, "observed_at": at.isoformat(),
               "expires_at": (at + timedelta(minutes=ttl_minutes)).isoformat() if state == "online" else "",
               "note": "Hook/self report, not independent proof of task acceptance."}
        _write(path, row)
        notice = None
        if previous != state:
            transition = ("joined" if previous == "unknown" and state == "online" else
                          "rejoined" if state == "online" else "left")
            notice = _notice(root, agent=agent, transition=transition, state=state,
                             previous=previous, source=source, at=at)
        return {"agent": agent, "state": state, "previous": previous,
                "notice": notice, "unread": unread(root, agent)["count"]}


def reconcile(root: Path, *, at: datetime | None = None) -> dict:
    root = Path(root)
    at = at or _utc()
    notices = []
    with _lock(root):
        for path in sorted((root / "presence" / "agents").glob("*.json")):
            row = _read(path)
            if row.get("state") == "online" and effective(row, at) == "stale":
                row["state"] = "stale"
                row["stale_at"] = at.isoformat()
                _write(path, row)
                notices.append(_notice(root, agent=row["agent"], transition="lost_contact",
                                       state="stale", previous="online", source="ttl_deadline", at=at))
    return {"notices": notices}


def snapshot(root: Path) -> dict:
    root = Path(root)
    agents = []
    for path in sorted((root / "presence" / "agents").glob("*.json")):
        row = _read(path)
        agent = row.get("agent")
        if not isinstance(agent, str) or not NAME.fullmatch(agent):
            continue
        agents.append({"agent": agent, "state": effective(row), "source": row.get("source"),
                       "observed_at": row.get("observed_at"), "expires_at": row.get("expires_at"),
                       "unread_count": unread(root, agent)["count"],
                       "active_assignments": _active_assignments(root, agent)})
    return {"agents": agents, "strength": {state: sum(a["state"] == state for a in agents)
                                            for state in ("online", "offline", "stale")},
            "note": "Only online with fresh evidence is considered available; acceptance still needs ACK."}


def next_due(root: Path) -> str:
    times = []
    for path in (Path(root) / "presence" / "agents").glob("*.json"):
        row = _read(path)
        if row.get("state") == "online" and row.get("expires_at"):
            times.append(row["expires_at"])
    return min(times) if times else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("signal", "reconcile", "status", "next-due"))
    parser.add_argument("--root", type=Path, default=Path.cwd() / "coordination")
    parser.add_argument("--agent", default="")
    parser.add_argument("--state", choices=("online", "offline"), default="online")
    parser.add_argument("--source", default="manual")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--ttl-minutes", type=int, default=30)
    args = parser.parse_args()
    if args.action == "signal":
        value = signal(args.root, agent=args.agent, state=args.state, source=args.source,
                       session_id=args.session_id, reason=args.reason, ttl_minutes=args.ttl_minutes)
    elif args.action == "reconcile":
        value = reconcile(args.root)
    elif args.action == "status":
        value = snapshot(args.root)
    else:
        value = {"next_due_at": next_due(args.root)}
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
