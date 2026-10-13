"""Read-only, evidence-graded audit of local agent messages.

Mailbox presence, mailbox read receipt, desktop visibility and agent acceptance
are separate facts. This command never wakes a model or retries delivery.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def audit(root: Path, *, topic: str = "", agent: str = "") -> dict:
    root = Path(root)
    states: dict[tuple[str, str], dict] = {}
    for path in (root / "commander" / "state").glob("*.json"):
        row = _read(path)
        key = (str(row.get("target", "")), str(row.get("event_id", "")))
        if not all(key):
            continue
        if key not in states or str(row.get("updated_at", "")) > str(states[key].get("updated_at", "")):
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
        rows.append({
            "id": message_id,
            "to": target,
            "topic": msg.get("topic", ""),
            "kind": msg.get("kind", ""),
            "mailbox": "DEPOSITED",
            "mailbox_read": read_ack.get("id") == message_id and read_ack.get("agent") == target,
            "desktop": desktop,
            "agent_accepted": desktop == "AGENT_ACK" and bool(state.get("ack")),
            "order_id": state.get("id", ""),
            "last_attempt": (state.get("attempts") or [{}])[-1].get("note", ""),
        })

    counts = Counter(row["desktop"] for row in rows)
    return {
        "messages": len(rows),
        "mailbox_read": sum(row["mailbox_read"] for row in rows),
        "message_visible": counts["MESSAGE_VISIBLE"] + counts["AGENT_ACK"],
        "agent_accepted": sum(row["agent_accepted"] for row in rows),
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
