"""Join one explicitly selected, previously bound workspace.

The caller declares its identity and session. This module does not start or
locate a client, authenticate the declaration, signal presence, or send a task.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import join_journal, workspace_binding, workspace_members


def join(coord_root: Path, project_root: Path, *, workspace_id: str,
         agent: str, session_id: str = "") -> dict:
    """Validate an explicit existing binding before recording membership."""
    if not workspace_id:
        raise ValueError("WORKSPACE_ID_REQUIRED")
    binding = workspace_binding.lookup(coord_root, project_root, workspace_id)
    membership = workspace_members.join(
        coord_root, workspace_id=binding["workspace_id"], agent=agent,
        source="shared-workspace", session_id=session_id,
    )
    # Membership and JSON journal are distinct stores.  Repeating this join
    # retries the same sequence with a deterministic ID, even if membership
    # was already committed before the previous projection failed.
    seq = membership["event_seq"]
    try:
        projected = join_journal.project(coord_root, workspace_id=binding["workspace_id"],
                                         after=seq - 1, limit=1)
        records = projected["events"]
        if len(records) != 1 or records[0]["sequence"] != seq:
            raise ValueError("JOIN_EVENT_NOT_PROJECTED")
        journal = {"status": "RECORDED", **records[0]}
    except Exception as exc:
        # Preserve the already committed membership and surface the pending
        # work; do not report journal publication or agent notification.
        journal = {"status": "PENDING", "sequence": seq,
                   "error_type": type(exc).__name__, "error": str(exc)}
    return {"schema": "aep-bound-workspace-join-v1", "binding": binding,
            "membership": membership, "journal_projection": journal, "presence_signalled": False,
            "notification_delivered": False, "agent_ack": False,
            "note": "Self-declared member and session; no client or delivery verified."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coord-root", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--session-id", default="")
    args = parser.parse_args()
    result = join(args.coord_root, args.project_root, workspace_id=args.workspace_id,
                  agent=args.agent, session_id=args.session_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
