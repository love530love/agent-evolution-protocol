"""Read-only, evidence-graded audit of local agent messages.

Mailbox presence, mailbox read receipt, desktop visibility and agent acceptance
are separate facts. This command never wakes a model or retries delivery.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from ..isotime import parse_iso_or_none


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _state_time(row: dict) -> datetime | None:
    """Parse updated_at as an aware UTC instant; malformed values lose to valid ones."""
    value = row.get("updated_at", "")
    if not isinstance(value, str):
        return None
    parsed = parse_iso_or_none(value, assume_utc=True)
    if parsed is None:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _newer_state(current: dict, candidate: dict) -> bool:
    current_time, candidate_time = _state_time(current), _state_time(candidate)
    if candidate_time is None:
        return False
    if current_time is None:
        return True
    return candidate_time > current_time


def audit(root: Path, *, topic: str = "", agent: str = "", now: datetime | None = None) -> dict:
    root = Path(root)
    moment = datetime.now(timezone.utc) if now is None else now
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    states: dict[tuple[str, str], dict] = {}
    for path in (root / "commander" / "state").glob("*.json"):
        row = _read(path)
        key = (str(row.get("target", "")), str(row.get("event_id", "")))
        if not all(key):
            continue
        if key not in states or _newer_state(states[key], row):
            states[key] = row

    rows: list[dict] = []
    for path in sorted((root / "messages").glob("*/*.json")):
        msg = _read(path)
        target = str(msg.get("to", ""))
        message_id = str(msg.get("id", ""))
        if not target or not message_id or path.parent.name != target or path.stem != message_id:
            continue
        if agent and target != agent:
            continue
        if topic and msg.get("topic") != topic:
            continue
        state = states.get((target, message_id), {})
        desktop = str(state.get("state", "")) or "NOT_ATTEMPTED"
        # The mailbox read ACK is not the same thing as accepting a task.
        read_ack = _read(root / "acks" / target / f"{message_id}.json")
        accepted = desktop == "AGENT_ACK" and bool(state.get("ack"))
        next_required = ("ARTIFACT_VERIFIED" if accepted else
                         "AGENT_ACK" if desktop == "MESSAGE_VISIBLE" else
                         "MESSAGE_VISIBLE" if desktop == "INPUT_SUBMITTED" else
                         "INPUT_SUBMITTED" if desktop == "WINDOW_FOUND" else
                         "CLIENT_BOUND" if desktop == "DEPOSITED" or desktop == "NOT_ATTEMPTED" else
                         "INSPECT_UNKNOWN")
        rows.append({
            "id": message_id,
            "to": target,
            "topic": msg.get("topic", ""),
            "kind": msg.get("kind", ""),
            "mailbox": "DEPOSITED",
            "mailbox_read": read_ack.get("id") == message_id and read_ack.get("agent") == target,
            "desktop": desktop,
            "agent_accepted": accepted,
            "next_required": next_required,
            "order_id": state.get("id", ""),
            "last_attempt": (state.get("attempts") or [{}])[-1].get("note", ""),
        })

    counts = Counter(row["desktop"] for row in rows)
    clock_skew_suspect = any(
        (stamp - moment).total_seconds() > 60
        for row in states.values()
        if (stamp := _state_time(row)) is not None
    )
    return {
        "clock_skew_suspect": clock_skew_suspect,
        "messages": len(rows),
        "mailbox_read": sum(row["mailbox_read"] for row in rows),
        "message_visible": counts["MESSAGE_VISIBLE"] + counts["AGENT_ACK"],
        "agent_accepted": sum(row["agent_accepted"] for row in rows),
        "next_required": dict(sorted(Counter(row["next_required"] for row in rows).items())),
        "ceased": counts["CEASED"],
        "desktop_states": dict(sorted(counts.items())),
        "rows": rows,
        "note": "A mailbox file is not a read receipt; MESSAGE_VISIBLE is not an Agent ACK.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--topic", default="")
    parser.add_argument("--agent", default="")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    result = audit(args.root, topic=args.topic, agent=args.agent)
    if args.summary_only:
        result.pop("rows")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
