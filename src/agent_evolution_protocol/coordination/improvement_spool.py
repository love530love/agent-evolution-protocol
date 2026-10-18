"""Durable completion-event inbox. No model calls, desktop input or test execution."""
import hashlib
import json
from pathlib import Path

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
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            receipt = root / "improvement_spool" / "receipts" / path.name
            if receipt.exists() and json.loads(receipt.read_text(encoding="utf-8")).get("source_sha256") == digest:
                continue
            try:
                proposal = improvement.ingest(root, path)
                result = {"status": "PROPOSED", "id": proposal["id"]}
            except (OSError, ValueError, KeyError, TypeError) as exc:
                result = {"status": "REJECTED", "error": str(exc)}
            result.update(source_sha256=digest, source=str(path.resolve()))
            _atomic_json(receipt, result)
            results.append(result)
    return {"processed": len(results), "results": results, "execution_started": False}
