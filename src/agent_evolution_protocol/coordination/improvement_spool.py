"""Durable completion-event inbox. No model calls, desktop input or test execution."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from . import improvement
from .battle_orders import _atomic_json, _task_writer_lock


def enqueue(root, event_file):
    root = Path(root)
    event = json.loads(Path(event_file).read_text(encoding="utf-8"))
    if not isinstance(event, dict) or set(event) != {"event_id", "kind", "improvement"}:
        raise ValueError("invalid completion event")
    if event["kind"] not in {"TASK_COMPLETED", "TASK_FAILED"}:
        raise ValueError("unsupported completion event")
    if not isinstance(event["event_id"], str) or not event["event_id"].strip():
        raise ValueError("event_id required")
    key = hashlib.sha256(event["event_id"].encode()).hexdigest()
    path = root / "improvement_spool" / "inbox" / f"{key}.json"
    with _task_writer_lock(root, "improvement-spool"):
        if path.exists():
            if json.loads(path.read_text(encoding="utf-8")) != event:
                raise ValueError("event id reused with changed payload")
        else:
            _atomic_json(path, event)
    return {"queued": True, "event_id": event["event_id"], "path": str(path.resolve())}


def drain(root):
    root = Path(root)
    results = []
    with _task_writer_lock(root, "improvement-spool"):
        for path in sorted((root / "improvement_spool" / "inbox").glob("*.json")):
            receipt = root / "improvement_spool" / "receipts" / path.name
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if receipt.exists():
                    try:
                        prior = json.loads(receipt.read_text(encoding="utf-8"))
                    except (ValueError, OSError):
                        prior = None
                    valid = isinstance(prior, dict) and prior.get("status") in {"PROPOSED", "REJECTED"}
                    if not valid:
                        _atomic_json(root / "improvement_spool" / "damaged_receipts" / f"{uuid4().hex}.json",
                                     {"source": str(receipt.resolve()),
                                      "content": receipt.read_text(encoding="utf-8", errors="replace")})
                    if valid and prior.get("source_sha256") == digest:
                        continue
                proposal = improvement.ingest(root, path)
                result = {"status": "PROPOSED", "id": proposal["id"]}
            except (OSError, ValueError, KeyError, TypeError) as exc:
                result = {"status": "REJECTED", "error": str(exc)}
                if not path.is_file():
                    continue  # Removed by an external actor; reconcile the remaining inbox.
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result.update(source_sha256=digest, source=str(path.resolve()))
            _atomic_json(receipt, result)
            results.append(result)
    return {"processed": len(results), "results": results, "execution_started": False}


def overview(root):
    """Read-only queue and proposal health; a receipt is not an Agent ACK."""
    root = Path(root)
    counts = {"pending": 0, "processed": 0, "rejected": 0, "damaged": 0}
    messages = []
    for path in sorted((root / "improvement_spool" / "inbox").glob("*.json")):
        state = "pending"
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            receipt = root / "improvement_spool" / "receipts" / path.name
            if receipt.exists():
                row = json.loads(receipt.read_text(encoding="utf-8"))
                if not isinstance(row, dict):
                    raise ValueError("invalid receipt")
                if row.get("source_sha256") == digest:
                    state = {"PROPOSED": "processed", "REJECTED": "rejected"}.get(row.get("status"), "damaged")
        except (OSError, ValueError, TypeError):
            state = "damaged"
        counts[state] += 1
        messages.append({"file": path.name, "state": state})
    next_actions = {"PROPOSED": "validate", "VALIDATED": "independent_review", "REVIEWED": "trial",
                    "TRIAL": "assess_trial", "ADOPTED": "monitor", "REJECTED": "closed", "ROLLED_BACK": "retrospect"}
    proposals = []
    for folder in sorted((root / "improvements").glob("*")):
        if not folder.is_dir():
            continue
        try:
            row = improvement.status(root, folder.name)
            proposals.append({"id": folder.name, "state": row["state"],
                              "next_action": next_actions.get(row["state"], "inspect")})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            proposals.append({"id": folder.name, "state": "DAMAGED", "error": str(exc), "next_action": "inspect"})
    return {"queue": counts, "messages": messages, "proposals": proposals,
            "agent_ack_verified": False, "execution_started": False}
