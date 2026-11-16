"""Read-only health verdict for AEP's resident Windows control planes.

This is intentionally a *deployment* status, not a delivery receipt.  A live
Core Host proves durable coordination; a live Desktop Host proves that an
interactive loop is present.  Neither proves that a specific original session
accepted an order.  That final claim still belongs to the order receipt and its
anchored ``AEP-ACK`` evidence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "INVALID", "reason": f"{type(exc).__name__}: {exc}"}
    return value if isinstance(value, dict) else {"status": "INVALID", "reason": "NOT_OBJECT"}


def _plane(name: str, row: dict[str, Any] | None, *, now: datetime, max_age_seconds: float) -> dict[str, Any]:
    if row is None:
        return {"plane": name, "status": "MISSING", "healthy": False}
    if row.get("status") == "INVALID":
        return {"plane": name, "status": "INVALID", "healthy": False, "reason": row["reason"]}
    raw_time = row.get("completed_at") or row.get("at")
    try:
        at = datetime.fromisoformat(str(raw_time))
        if at.tzinfo is None:
            raise ValueError("timezone required")
        age = max(0.0, (now - at.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return {"plane": name, "status": "INVALID", "healthy": False,
                "reason": "HEARTBEAT_TIMESTAMP_INVALID", "heartbeat": row}
    healthy = row.get("status") in {"STARTED", "CYCLE_COMPLETED", "TICK_COMPLETED"} and age <= max_age_seconds
    return {"plane": name, "status": "HEALTHY" if healthy else "STALE_OR_FAILED",
            "healthy": healthy, "age_seconds": round(age, 3), "heartbeat": row}


def status(root: Path, *, max_age_seconds: float = 90.0,
           now: datetime | None = None) -> dict[str, Any]:
    """Read all resident-plane heartbeats without starting tasks or clients."""
    if isinstance(max_age_seconds, bool) or max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    now = now or datetime.now(timezone.utc)
    runtime = Path(root) / "runtime"
    core = _plane("core", _read(runtime / "aep-core-host.heartbeat.json"),
                  now=now, max_age_seconds=float(max_age_seconds))
    desktop = _plane("desktop", _read(runtime / "aep-supervisor.heartbeat.json"),
                     now=now, max_age_seconds=float(max_age_seconds))
    high_integrity = _plane("elevated", _read(runtime / "aep-elevated-worker.heartbeat.json"),
                            now=now, max_age_seconds=float(max_age_seconds))
    # Elevated workers are one-shot by design: their absence is normal, unlike
    # core/desktop.  It becomes relevant only for a particular high-integrity
    # order and must not make the whole control plane look dead.
    required = (core, desktop)
    return {
        "schema": "aep-resident-status-v1", "at": now.isoformat(),
        "coord_root": str(Path(root).resolve()), "max_age_seconds": max_age_seconds,
        "planes": {row["plane"]: row for row in (core, desktop, high_integrity)},
        "control_plane": "READY" if all(row["healthy"] for row in required) else "HOLD",
        "delivery_claim": "NONE",
        "note": "READY means resident coordination loops are healthy; it does not prove a message was delivered or acknowledged.",
        "execution_started": False,
    }
