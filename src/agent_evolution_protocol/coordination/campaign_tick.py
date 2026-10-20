"""Batch local campaign maintenance in one Python process; no model or UI calls."""
import argparse
import json
from pathlib import Path

from . import campaign_cycle, command_memory, presence, team_dispatch


def tick(root, bridge_root):
    results = {"presence": presence.reconcile(root), "warnings": []}
    operations = (
        ("delegation", lambda: team_dispatch.process(root, bridge_root)),
        ("command_memory", lambda: command_memory.ingest_journal(root)),
        ("bridge_command_memory", lambda: command_memory.ingest_journal(root, journal_root=bridge_root)),
    )
    for name, operation in operations:
        try:
            results[name] = operation()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            results["warnings"].append({"stage": name, "error": str(exc)})
    # Deadline processing remains required even when optional knowledge ingestion fails.
    results["campaign"] = campaign_cycle.reconcile(root, bridge_root)
    return results


def next_due(root):
    deadlines = [value for value in (campaign_cycle.next_due(root), presence.next_due(root)) if value]
    # ISO strings can have different offsets; compare actual times.
    from datetime import datetime
    return {"next_due_at": min(deadlines, key=datetime.fromisoformat) if deadlines else ""}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("tick", "next-due"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--bridge-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = tick(args.root, args.bridge_root) if args.action == "tick" else next_due(args.root)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
