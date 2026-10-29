"""Bounded, event-id based acknowledgement tracking."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dir(root: Path) -> Path:
    p = Path(root) / "clients" / "acks"; p.mkdir(parents=True, exist_ok=True); return p


def open_request(root: Path, *, event_id: str, agent: str, lease_seconds: int = 300) -> dict:
    existing = read(root, event_id)
    if existing and existing.get("state") in {"ack_pending", "acknowledged"}:
        raise ValueError("duplicate event_id: existing request is still live")
    row = {"event_id": event_id, "agent": agent, "state": "ack_pending",
           "opened_at": _now(), "lease_seconds": int(lease_seconds)}
    (_dir(root) / f"{event_id}.json").write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return row


def acknowledge(root: Path, *, event_id: str, agent: str, detail: str = "") -> dict:
    p = _dir(root) / f"{event_id}.json"
    if not p.exists():
        raise ValueError("unknown event_id")
    row = json.loads(p.read_text(encoding="utf-8"))
    if row.get("agent") != agent:
        raise ValueError("ack agent mismatch")
    row.update({"state": "acknowledged", "ack_at": _now()})
    if detail: row["detail"] = detail
    p.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return row


def read(root: Path, event_id: str) -> dict | None:
    p = _dir(root) / f"{event_id}.json"
    if not p.exists(): return None
    return json.loads(p.read_text(encoding="utf-8"))


def wait_for_ack(root: Path, *, event_id: str, timeout_seconds: float = 0,
                 interval_seconds: float = 0.25) -> dict:
    """Wait only for a known request; zero timeout is a non-blocking read."""
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        row = read(root, event_id)
        if row is None: return {"state": "unknown", "event_id": event_id}
        if row.get("state") == "acknowledged": return row
        if time.monotonic() >= deadline:
            row["state"] = "ack_timeout"
            p = _dir(root) / f"{event_id}.json"
            p.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            return row
        time.sleep(min(max(interval_seconds, 0.05), 1.0))
