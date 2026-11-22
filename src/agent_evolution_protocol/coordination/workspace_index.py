"""Read-only workspace index: a shared directory for any joining agent.

The coordination area is a group chat plus a catalogue.  Any agent that joins
needs to answer four questions without private knowledge:

  1. who is here (the seat directory, including seats kept after a member
     goes offline),
  2. who just joined (newcomer discovery),
  3. what was said (the chat event stream), and
  4. what we know (a read-only, parameterised query over the knowledge store).

This module is deliberately a *view* layer.  It reuses the existing standards
instead of inventing a second one:

  * ``presence.snapshot``  -> seat state (online / stale / offline), never ACK,
  * ``conversation_journal`` -> chat events,
  * ``knowledge_store.query`` -> read-only SQLite over records,
  * ``role_contract`` -> the role catalogue (when present).

Hard edges:
  * read-only: no function here writes a file or a database; an absent store
    is an empty index, never a newly-created schema;
  * fail-closed: malformed rows are skipped, not guessed; state never becomes
    "accepted" just because it is visible;
  * deterministic: no wall clock in ids; ``at`` is injectable for tests.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent_evolution_protocol.coordination import collab_health, knowledge_store, presence
from agent_evolution_protocol.coordination.conversation_journal import read_events
from agent_evolution_protocol.coordination.workspace_identity import validate_workspace_id

SEAT_SCHEMA = "aep-workspace-seat-v1"
INDEX_SCHEMA = "aep-workspace-index-v1"
QUERY_SCHEMA = "aep-knowledge-query-v1"

# A presence row is a kept seat for this long after its evidence expires.
# This is a *reporting* convention only; presence.reconcile owns the real TTL.
SEAT_KEEP_AFTER_EXPIRY = timedelta(days=7)


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _parse(moment: Any) -> datetime | None:
    if not isinstance(moment, str) or not moment:
        return None
    try:
        parsed = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _names(root: Path) -> set[str]:
    """Every agent with a seat in the workspace.

    A seat is kept even when its occupant is offline, so we union the identity
    sources rather than only reading fresh presence: presence rows, session
    bindings, and mailbox directories all count as 'has a seat'.
    """
    names: set[str] = set()
    for folder in ("presence/agents", "sessions", "messages", "mailbox"):
        base = root / folder
        if not base.exists():
            continue
        for path in base.iterdir():
            if folder == "presence/agents" or folder == "sessions":
                if path.is_file() and path.suffix == ".json":
                    names.add(path.stem)
            elif path.is_dir():
                names.add(path.name)
    return {name for name in names if name and not name.startswith(".")}


def members(root: Path, *, at: datetime | None = None) -> dict:
    """Seat directory.  Offline/seat information is reported, never promoted."""
    root = Path(root)
    now = at or datetime.now(timezone.utc)
    snap = presence.snapshot(root)
    by_agent = {row["agent"]: row for row in snap["agents"]}

    seats = []
    for name in sorted(_names(root)):
        row = by_agent.get(name, {})
        state = row.get("state", "unknown")
        observed = _parse(row.get("observed_at"))
        expires = _parse(row.get("expires_at"))
        seat_kept = name in by_agent  # a presence file is a retained seat
        if expires and now - expires > SEAT_KEEP_AFTER_EXPIRY:
            seat_kept = False
        seats.append({
            "agent": name,
            "state": state,
            "source": row.get("source", ""),
            "observed_at": row.get("observed_at", ""),
            "expires_at": row.get("expires_at", ""),
            "seat_kept": seat_kept,
            "unread_count": row.get("unread_count", 0),
            "active_assignments": row.get("active_assignments", []),
        })
    online = sum(s["state"] == "online" for s in seats)
    return {
        "schema": SEAT_SCHEMA,
        "seats": seats,
        "total": len(seats),
        "online": online,
        "note": ("A retained seat or a visible message is NOT agent acceptance. "
                 "Only VERIFIED / AGENT_ACK closes a task."),
    }


def newcomers(root: Path, *, within_hours: float = 24.0,
              at: datetime | None = None) -> dict:
    """Report earliest recorded join notices, never the latest heartbeat.

    Notices are scoped to this coordination root, not a workspace watermark.
    Missing historical notices do not prove that a member never joined.
    """
    root = Path(root)
    now = at or datetime.now(timezone.utc)
    if (isinstance(within_hours, bool) or not isinstance(within_hours, (int, float))
            or not math.isfinite(within_hours) or within_hours <= 0):
        raise ValueError("within_hours must be a positive finite number")
    if now.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    cutoff = now - timedelta(hours=within_hours)
    first: dict[str, tuple[datetime, Path, dict]] = {}
    for path in sorted((root / "presence" / "notices").glob("*.json")):
        row = _read_json(path)
        if not row or row.get("transition") != "joined":
            continue
        agent = row.get("agent")
        observed = _parse(row.get("at"))
        if (not isinstance(agent, str) or not presence.NAME.fullmatch(agent)
                or observed is None or observed > now):
            continue
        if agent not in first or observed < first[agent][0]:
            first[agent] = (observed, path, row)
    found = []
    for agent, (observed, path, row) in sorted(first.items(), key=lambda item: (item[1][0], item[0])):
        if observed < cutoff:
            continue
        current = _read_json(root / "presence" / "agents" / (agent + ".json")) or {}
        found.append({"agent": agent, "observed_at": row.get("at"),
                      "source": row.get("source", ""),
                      "state": presence.effective(current, at=now),
                      "evidence": str(path)})
    return {
        "schema": INDEX_SCHEMA,
        "view": "newcomers",
        "within_hours": within_hours,
        "joined": found,
        "count": len(found),
        "note": ("Earliest available join notice in this coordination root; missing history is unknown. "
                 "Presence is not proof of workspace membership or task acceptance."),
    }


def chat(root: Path, *, after: str = "", limit: int = 50,
         workspace_id: str = "", room: str = "", contains: str = "") -> dict:
    """Read the journal, optionally scoped to explicit workspace watermarks.

    Events without a workspace watermark are excluded from a scoped query.
    Cursor advancement includes skipped events, so filtered pages do not
    repeatedly scan the same unrelated records.
    """
    root = Path(root)
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if workspace_id:
        validate_workspace_id(workspace_id)
    events = []
    cursor = after
    while len(events) < limit:
        page = read_events(root, after=cursor, limit=500)
        if not page:
            break
        for event in page:
            cursor = event["id"]
            if workspace_id and event.get("workspace_id") != workspace_id:
                continue
            if room and event.get("room") != room:
                continue
            if contains and contains.casefold() not in str(event.get("text", "")).casefold():
                continue
            events.append(event)
            if len(events) == limit:
                break
        if len(page) < 500:
            break
    return {
        "schema": INDEX_SCHEMA,
        "view": "chat",
        "events": events,
        "count": len(events),
        "next_cursor": cursor,
        "workspace_id": workspace_id or None,
        "scope": "workspace" if workspace_id else "coordination-root-unfiltered",
        "note": ("Unwatermarked events are excluded when workspace_id is set; "
                 "unfiltered coordination-root results may span workspaces. "
                 "Chat visibility is not an ACK and does not advance a task."),
    }


def knowledge(path: Path, *, kind: str = "", actor: str = "", task_id: str = "",
              contains: str = "", limit: int = 50) -> dict:
    """Parameterised read-only query; no caller SQL reaches sqlite."""
    return knowledge_store.query(path, kind=kind, actor=actor, task_id=task_id,
                                 contains=contains, limit=limit)


def roles(root: Path) -> dict:
    """Role catalogue when the contract module and state are present."""
    root = Path(root)
    try:
        from agent_evolution_protocol.coordination import role_contract
        catalogue = role_contract.ROLES
        return {"schema": INDEX_SCHEMA, "view": "roles",
                "roles": catalogue, "count": len(catalogue),
                "note": "Roles are functional names, not vendor or session identities."}
    except (ImportError, AttributeError) as exc:
        return {"schema": INDEX_SCHEMA, "view": "roles", "roles": {},
                "count": 0, "unavailable": f"{type(exc).__name__}"}


def health(root: Path, *, at: datetime | None = None) -> dict:
    """Active online query + offline detection from the resident layers.

    Reuses ``collab_health`` four-layer snapshot.  An expired presence row is
    reported stale, never offline; a heartbeat pid is not proof of a live OS
    process.  This is the read-only query side of the seat/health mechanism.
    """
    root = Path(root)
    snap = collab_health.snapshot(root, now=at)
    return {"schema": INDEX_SCHEMA, "view": "health",
            "verdict": snap.get("verdict"),
            "online_layers": snap.get("online_layers", {}),
            "hosts": snap.get("hosts", {}),
            "note": ("READY means fresh online evidence and healthy hosts; it is "
                     "not a delivery or ACK claim. Stale is never offline.")}


def overview(root: Path, *, knowledge_db: Path | None = None,
             at: datetime | None = None) -> dict:
    """One-page index: the first thing a joining agent reads."""
    root = Path(root)
    seat_view = members(root, at=at)
    health_view = health(root, at=at)
    return {
        "schema": INDEX_SCHEMA,
        "view": "overview",
        "members": {"total": seat_view["total"], "online": seat_view["online"],
                    "seats_kept": sum(s["seat_kept"] for s in seat_view["seats"])},
        "health_verdict": health_view["verdict"],
        "recent_chat_count": len(chat(root, limit=50)["events"]),
        "knowledge": knowledge(knowledge_db or (root / "knowledge.sqlite")),
        "roles": roles(root)["count"],
        "note": "Read-only index. Advisory only; it never advances or closes a task.",
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="read-only workspace index")
    parser.add_argument("view", choices=("overview", "members", "newcomers",
                                          "chat", "knowledge", "roles", "health"))
    parser.add_argument("--root", type=Path, default=Path("coordination"))
    parser.add_argument("--db", type=Path, default=None, help="knowledge sqlite path")
    parser.add_argument("--within-hours", type=float, default=24.0)
    parser.add_argument("--after", default="")
    parser.add_argument("--workspace-id", default="", help="explicit journal workspace watermark")
    parser.add_argument("--room", default="", help="exact journal room")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--kind", default=""); parser.add_argument("--actor", default="")
    parser.add_argument("--task-id", default=""); parser.add_argument("--contains", default="")
    args = parser.parse_args()
    if args.view == "overview":
        value = overview(args.root, knowledge_db=args.db)
    elif args.view == "members":
        value = members(args.root)
    elif args.view == "newcomers":
        value = newcomers(args.root, within_hours=args.within_hours)
    elif args.view == "chat":
        value = chat(args.root, after=args.after, limit=args.limit,
                     workspace_id=args.workspace_id, room=args.room, contains=args.contains)
    elif args.view == "knowledge":
        value = knowledge(args.db or (args.root / "knowledge.sqlite"),
                          kind=args.kind, actor=args.actor, task_id=args.task_id,
                          contains=args.contains, limit=args.limit)
    elif args.view == "roles":
        value = roles(args.root)
    else:
        value = health(args.root)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
