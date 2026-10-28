"""Scoped wake queue -> commander handoff, with durable identity and no replay.

Both the legacy daemon and the CLI use this entrypoint. A queue entry means
pending work, never delivery or acceptance. Desktop actions remain in commander.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from . import commander
from .battle_orders import _task_writer_lock

EVENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
FOLDERS = ("wake_queue", "wake_processing", "wake_pending", "wake_done", "wake_failed")


def valid_event_id(value: str) -> str:
    if not isinstance(value, str) or not EVENT_ID.fullmatch(value):
        raise ValueError("invalid wake ID")
    return value


def wake_lock(root: Path, event_id: str):
    return _task_writer_lock(Path(root), "wake-" + valid_event_id(event_id))


def _read(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("wake request must be a JSON object")
    return data


def _source(root: Path, event_id: str) -> tuple[Path, dict]:
    paths = [root / folder / f"{event_id}.json" for folder in FOLDERS]
    found = [path for path in paths if path.is_file()]
    if len(found) != 1:
        raise ValueError("wake request missing or has multiple lifecycle copies; inspect before dispatch")
    event = _read(found[0])
    if event.get("id") != event_id or event.get("type") != "wake_request":
        raise ValueError("wake identity/type mismatch")
    return found[0], event


def _fingerprint(event: dict) -> str:
    return hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _order(root: Path, event: dict) -> dict | None:
    expected_id = "WAKE-" + event["id"]
    orders = [row for row in commander._orders(root) if row.get("event_id") == expected_id]
    if len(orders) > 1:
        raise ValueError("multiple orders for wake event; inspect before dispatch")
    if orders:
        source = orders[0].get("source_wake", {})
        if source != {"id": event["id"], "sha256": _fingerprint(event), "coord_root": str(root)}:
            raise ValueError("wake source changed or event ID collision; no input attempted")
        return orders[0]
    return None


def _preview(root: Path, event: dict, order: dict | None) -> dict:
    # Reject non-boolean flags instead of treating strings such as 'false' as consent.
    for key in ("dry_run", "requires_user_auth", "allow_new_session"):
        if key in event and type(event[key]) is not bool:
            raise ValueError(f"{key} must be boolean")
    for key in ("sender", "to"):
        if not isinstance(event.get(key), str) or not commander.SAFE_AGENT.fullmatch(event[key]):
            raise ValueError(f"invalid wake {key}")
    if not isinstance(event.get("task"), str) or not event["task"].strip():
        raise ValueError("wake task is required")
    text = event.get("deliver_text") or event.get("reason")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("wake delivery text/reason is required")
    state = (order or {}).get("state", "QUEUED")
    if event.get("cancelled"):
        state = "CANCELLED_BEFORE_CONSUMPTION"
    elif event.get("dry_run"):
        state = "DRY_RUN"
    elif event.get("requires_user_auth"):
        state = "AUTHORIZATION_REQUIRED"
    elif event.get("allow_new_session"):
        state = "NEW_SESSION_UNSUPPORTED"
    return {"id": event["id"], "event_id": "WAKE-" + event["id"],
            "coord_root": str(root), "target": event["to"], "task": event["task"],
            "order_id": (order or {}).get("id"), "state": state,
            "dispatch_attempted": False,
            "task_delivered": state in {"MESSAGE_VISIBLE", "AGENT_ACK"},
            "agent_accepted": state == "AGENT_ACK"}


def dispatch_wake(root: Path, event_id: str, *, apply: bool = False,
                  retry_deferred: bool = False) -> dict:
    """Preview by default; execute only this event with an explicit apply.

    A crashed consumer can recover the original source and unique order. Unknown
    input outcomes are never replayed; a proven pre-input DEFERRED requires an
    explicit retry. No other orders, clients or reinforcement jobs are ticked.
    """
    root = Path(root).resolve()
    valid_event_id(event_id)
    if not apply:
        _, event = _source(root, event_id)
        return _preview(root, event, _order(root, event))
    with wake_lock(root, event_id):
        path, event = _source(root, event_id)
        order = _order(root, event)
        result = _preview(root, event, order)
        if result["state"] in {"DRY_RUN", "AUTHORIZATION_REQUIRED", "NEW_SESSION_UNSUPPORTED",
                                "CANCELLED_BEFORE_CONSUMPTION"}:
            return result
        if path.parent.name in {"wake_done", "wake_failed", "wake_pending"} and order is None:
            raise ValueError("legacy wake already consumed; delivery unknown, no automatic replay")
        processing = root / "wake_processing" / path.name
        if path.parent.name == "wake_queue":
            processing.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, processing)
            path = processing
        if order is None:
            text = event.get("deliver_text") or event["reason"]
            token = f"[{commander.ACK_TOKEN}:{result['event_id']}]"
            if token not in text:
                text = f"{text.rstrip()}\n\n请在本会话回复 {token}，并给出结果与证据。"
            order = commander.submit_order(
                root, target=event["to"], sender=event["sender"], task=event["task"],
                text=text, event_id=result["event_id"], allow_launch=False,
                priority=event.get("priority", "normal"),
                source_wake={"id": event_id, "sha256": _fingerprint(event), "coord_root": str(root)})
        result["order_id"] = order["id"]
        if order["state"] == "DEPOSITED":
            result["dispatch_attempted"] = True
            order = commander.dispatch(root, order=order)
        elif order["state"] == "DEFERRED" and retry_deferred:
            result["dispatch_attempted"] = True
            order = commander.resume_deferred(root, order_id=order["id"])
        result.update(state=order["state"], task_delivered=order["state"] in {"MESSAGE_VISIBLE", "AGENT_ACK"},
                      agent_accepted=order["state"] == "AGENT_ACK")
        commander._atomic(root / "wake_receipts" / f"{event_id}.json", result)
        destination = root / ("wake_done" if result["task_delivered"] else "wake_pending") / path.name
        if path != destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, destination)
        return result


def cancel(root: Path, event_id: str, *, actor: str, reason: str) -> dict:
    """Cancel before handoff, sharing the consumer's lock (not just a rename)."""
    root = Path(root).resolve()
    if not reason.strip() or not commander.SAFE_AGENT.fullmatch(actor):
        raise ValueError("cancel needs a valid actor and reason")
    with wake_lock(root, event_id):
        queued = root / "wake_queue" / f"{event_id}.json"
        if not queued.is_file():
            raise ValueError("wake already consumed or missing; inspect its order before cancelling")
        event = _read(queued)
        if event.get("id") != event_id:
            raise ValueError("wake identity mismatch")
        if _order(root, event):
            raise ValueError("wake already handed to commander; cannot cancel as unconsumed")
        # Persist the cancellation flag before rename: a crash cannot restore a runnable item.
        commander._atomic(queued, {**event, "cancelled": True, "cancelled_by": actor,
                                  "cancel_reason": reason.strip(), "cancelled_at": commander.now()})
        failed = root / "wake_failed" / queued.name
        failed.parent.mkdir(parents=True, exist_ok=True)
        os.replace(queued, failed)
        return {"id": event_id, "state": "CANCELLED_BEFORE_CONSUMPTION",
                "adapter_executed": False, "execution_cancelled": False, "reason": reason.strip()}
