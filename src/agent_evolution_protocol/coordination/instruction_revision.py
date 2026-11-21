"""Explicit workspace/task instruction revisions for queued chat orders."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

from . import conversation_journal as journal
from .battle_orders import _atomic_json, _task_writer_lock
from .workspace_identity import validate_workspace_id


def _scope(workspace: str, task: str) -> str:
    validate_workspace_id(workspace)
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task is required")
    return sha256(json.dumps([workspace, task]).encode("utf-8")).hexdigest()


def lock(root: Path, workspace: str, task: str):
    return _task_writer_lock(root, "instruction-" + _scope(workspace, task))


def status(root: Path, workspace: str, task: str) -> dict:
    path = root / "instruction_revisions" / f"{_scope(workspace, task)}.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"workspace_id": workspace, "task": task, "history": []}
    if (record["workspace_id"] != workspace or record["task"] != task
            or not record["history"]):
        raise ValueError("invalid instruction revision record")
    return record


def activate(root: Path, workspace: str, task: str, *, event: str,
             expected: str, actor: str) -> dict:
    if not journal.SAFE_EVENT.fullmatch(event) or not journal.SAFE_AGENT.fullmatch(actor):
        raise ValueError("valid journal event and actor are required")
    path = journal.archive_root(root) / "events" / f"{event}.json"
    with lock(root, workspace, task):
        row = json.loads(path.read_text(encoding="utf-8"))
        if (row.get("id") != event or row.get("actor") != "user"
                or row.get("kind") != "message" or row.get("visibility") != "workspace"
                or not isinstance(row.get("text"), str) or not row["text"].strip()):
            raise ValueError("revision must reference a workspace user message")
        if "workspace_id" in row and row["workspace_id"] != workspace:
            raise ValueError("journal event belongs to a different workspace")
        record = status(root, workspace, task)
        history = record["history"]
        current = history[-1]["event_id"] if history else ""
        if current != expected:
            raise ValueError("current revision changed; read status before updating")
        if any(revision["event_id"] == event for revision in history):
            raise ValueError("previously activated events cannot be reactivated")
        history.append({"event_id": event, "actor": actor,
                        "sha256": sha256(row["text"].encode("utf-8")).hexdigest(),
                        "at": datetime.now(timezone.utc).isoformat()})
        _atomic_json(root / "instruction_revisions" / f"{_scope(workspace, task)}.json", record)
        return record


def stale(root: Path, order: dict) -> bool:
    record = status(root, order["workspace_id"], order["task"])
    if not record["history"]:
        return False
    current = record["history"][-1]
    source = order["source_chat"]
    return (current["event_id"], current["sha256"]) != (source["id"], source["sha256"])


def delivery_status(root: Path, workspace: str, task: str) -> dict:
    """List what each target may already have seen after a revision change."""
    from .commander_base import _orders, _preinput_retryable

    with lock(root, workspace, task):
        record = status(root, workspace, task)
        history = record["history"]
        current = history[-1] if history else None
        orders = [order for order in _orders(root) if order.get("source_chat")
                  and order.get("workspace_id") == workspace and order.get("task") == task]
        current_by_target = {}
        for order in orders:
            source = order["source_chat"]
            if current and (source["id"], source["sha256"]) == (current["event_id"], current["sha256"]):
                current_by_target.setdefault(order["target"], []).append(order)
        rows = []
        for order in orders:
            source = order["source_chat"]
            state = order["state"]
            observed = {state, *(item.get("state") for item in order.get("attempts", []))}
            correction = None
            if current is None or (current["event_id"], current["sha256"]) == (
                    source["id"], source["sha256"]):
                next_step = "TRACK_CURRENT"
            elif observed & {"MESSAGE_VISIBLE", "AGENT_ACK"}:
                next_step = "CHECK_CORRECTION_ACK"
                replacements = current_by_target.get(order["target"], [])
                if len(replacements) > 1:
                    next_step = "RECONCILE_DUPLICATE_CORRECTIONS"
                    correction = {"order_ids": [item["id"] for item in replacements]}
                elif replacements:
                    replacement = replacements[0]
                    correction = {"order_id": replacement["id"], "state": replacement["state"]}
                    if replacement["state"] == "AGENT_ACK" and replacement.get("ack"):
                        next_step = "VERIFY_CORRECTION_ADOPTION"
            elif _preinput_retryable(order):
                next_step = "SUPERSEDED_BEFORE_INPUT"
            else:
                next_step = "RECONCILE_OLD_DELIVERY"
            rows.append({"order_id": order["id"], "target": order["target"],
                         "journal_event_id": source["id"], "state": state,
                         "next_step": next_step, "correction": correction})
    return {"workspace_id": workspace, "task": task,
            "active_event_id": current["event_id"] if current else None, "orders": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--task", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("delivery-status")
    update = commands.add_parser("activate")
    update.add_argument("--event", required=True)
    update.add_argument("--expected", required=True, help="current event ID, or empty at initialization")
    update.add_argument("--actor", required=True)
    args = parser.parse_args()
    try:
        scope = (args.root, args.workspace_id, args.task)
        if args.command == "activate":
            report = activate(*scope, event=args.event, expected=args.expected, actor=args.actor)
        elif args.command == "delivery-status":
            report = delivery_status(*scope)
        else:
            report = status(*scope)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"instruction revision: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
