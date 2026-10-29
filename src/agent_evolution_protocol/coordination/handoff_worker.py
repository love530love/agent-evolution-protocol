"""Minimal agent-side handoff worker with evidence-backed acknowledgement.

This is the low-capability path for an agent that can run one command but has
no desktop automation.  The agent reads its own addressed mailbox message,
creates an analysis file, and runs this worker.  The worker verifies that the
message belongs to the named agent, stores an immutable analysis receipt, then
emits the normal delegation ACK.  It deliberately does *not* claim that the
analysis is correct or that a desktop model was woken.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from . import presence, team_dispatch


_PRIORITY_RANK = {"urgent": 0, "normal": 1}


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def accept_and_reply(root: Path, bridge_root: Path, *, agent: str, event_id: str,
                     session_id: str, analysis_file: Path) -> dict:
    """Consume one directed handoff and return an evidence-backed ACK.

    ``analysis_file`` is supplied by the receiving agent.  Its digest is kept
    in the durable artifact record so that an ACK cannot be mistaken for a
    bare "seen" acknowledgement.
    """
    root, bridge_root, analysis_file = Path(root), Path(bridge_root), Path(analysis_file)
    request_path = root / "delegation" / "inbox" / f"{event_id}.json"
    message_path = bridge_root / "messages" / agent / f"{event_id}.json"
    if not request_path.is_file() or not message_path.is_file():
        raise ValueError("HANDOFF_NOT_AVAILABLE")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    message = json.loads(message_path.read_text(encoding="utf-8"))
    if request.get("to") != agent or message.get("to") != agent:
        raise ValueError("HANDOFF_TARGET_MISMATCH")
    if message.get("id") != event_id or event_id not in str(message.get("text", "")):
        raise ValueError("HANDOFF_ENVELOPE_MISMATCH")
    if not analysis_file.is_file():
        raise ValueError("ANALYSIS_FILE_MISSING")
    content = analysis_file.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError("ANALYSIS_FILE_EMPTY")
    digest = sha256(content.encode("utf-8")).hexdigest()
    artifact = {
        "schema": "aep-handoff-analysis-v1", "event_id": event_id,
        "agent": agent, "session_id": session_id,
        "source": str(analysis_file.resolve()), "sha256": digest,
        "characters": len(content), "recorded_at": datetime.now(timezone.utc).isoformat(),
        "analysis": content,
    }
    artifact_path = root / "delegation" / "artifacts" / event_id / f"{agent}.json"
    if artifact_path.exists():
        prior = json.loads(artifact_path.read_text(encoding="utf-8"))
        if prior.get("sha256") != digest:
            raise ValueError("ANALYSIS_ARTIFACT_ALREADY_DIFFERENT")
    else:
        _atomic(artifact_path, artifact)
    # A real receiving agent must announce itself online before it can ACK.
    presence.signal(root, agent=agent, state="online", source="handoff-worker",
                    session_id=session_id)
    summary = " ".join(content.split())[:800]
    receipt = team_dispatch.acknowledge(
        root, bridge_root, event_id=event_id, agent=agent, session_id=session_id,
        accepted=True,
        reply=f"ACK {event_id}; analysis_sha256={digest}; analysis={summary}",
    )
    return {"state": receipt["state"], "event_id": event_id, "agent": agent,
            "artifact": str(artifact_path), "sha256": digest,
            "reply": receipt.get("reply", "")}


def inbox(root: Path, bridge_root: Path, *, agent: str) -> list[dict]:
    """List unfinished handoffs addressed to one agent.

    This is deliberately a pull-on-demand operation: it neither wakes a
    desktop application nor starts a polling loop.  The returned event id is
    all a low-capability agent needs to create an analysis and acknowledge it.
    """
    root, bridge_root = Path(root), Path(bridge_root)
    folder = bridge_root / "messages" / agent
    if not folder.is_dir():
        return []
    result = []
    for path in sorted(folder.glob("*.json")):
        try:
            message = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        event_id = str(message.get("id", ""))
        if message.get("kind") != "delegation" or message.get("to") != agent:
            continue
        receipt_path = root / "delegation" / "receipts" / f"{event_id}.json"
        if not receipt_path.is_file():
            continue
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("state") in {"AGENT_ACK", "AGENT_DECLINED", "REJECTED"}:
            continue
        # The durable delegation request carries the authoritative priority and
        # deadline.  The mailbox projection intentionally omits those fields
        # for legacy compatibility, so enrich the read-time view when the
        # request is available.  Missing/invalid metadata must not hide work:
        # it is surfaced as normal/unknown and remains actionable.
        request_path = root / "delegation" / "inbox" / f"{event_id}.json"
        request = {}
        if request_path.is_file():
            try:
                request = json.loads(request_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                request = {}
        priority = request.get("priority", "normal")
        if priority not in _PRIORITY_RANK:
            priority = "normal"
        due = request.get("reply_due_at", "")
        overdue = False
        if isinstance(due, str) and due:
            try:
                deadline = datetime.fromisoformat(due)
                overdue = deadline.tzinfo is not None and deadline <= datetime.now(timezone.utc)
            except ValueError:
                pass
        result.append({"event_id": event_id, "from": message.get("sender", ""),
                       "task": message.get("topic", ""), "text": message.get("text", ""),
                       "state": receipt.get("state", ""), "priority": priority,
                       "reply_due_at": due, "overdue": overdue,
                       "requires_immediate_read": priority == "urgent" or overdue})
    # Timeliness is a read-time concern, not a background polling policy:
    # urgent/overdue items first, then the nearest deadline, then event id for
    # deterministic replay across agents.
    def sort_key(row):
        due = row.get("reply_due_at", "")
        try:
            due_key = datetime.fromisoformat(due).timestamp() if due else float("inf")
        except ValueError:
            due_key = float("inf")
        return (_PRIORITY_RANK.get(row.get("priority", "normal"), 1), due_key,
                row.get("event_id", ""))
    return sorted(result, key=sort_key)


def main() -> int:
    # Windows PowerShell sessions may expose a cp1252 stdout even when the
    # workspace contains Chinese task text.  Reconfigure at the CLI boundary
    # so a real inbox read cannot fail after doing all the right work.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("inbox", "accept"), default="accept")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bridge-root", type=Path, required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--event-id", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--analysis-file", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "inbox":
            result = {"agent": args.agent, "pending": inbox(args.root, args.bridge_root, agent=args.agent)}
        else:
            if not args.event_id or args.analysis_file is None:
                raise ValueError("--event-id and --analysis-file are required for accept")
            result = accept_and_reply(args.root, args.bridge_root, agent=args.agent,
                                      event_id=args.event_id, session_id=args.session_id,
                                      analysis_file=args.analysis_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
