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
from math import isfinite
import os
from pathlib import Path
from typing import Any

from . import process_identity
from ..isotime import parse_iso_or_none


def _read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "INVALID", "reason": f"{type(exc).__name__}: {exc}"}
    return value if isinstance(value, dict) else {"status": "INVALID", "reason": "NOT_OBJECT"}


def _plane(name: str, row: dict[str, Any] | None, *, now: datetime, max_age_seconds: float,
           healthy_statuses: set[str] | None = None) -> dict[str, Any]:
    if row is None:
        return {"plane": name, "status": "MISSING", "healthy": False,
                "heartbeat_fresh": None, "pid_alive": None, "pid_identity": "UNKNOWN"}
    if row.get("status") == "INVALID":
        return {"plane": name, "status": "INVALID", "healthy": False, "reason": row["reason"],
                "heartbeat_fresh": None, "pid_alive": None, "pid_identity": "UNKNOWN"}
    raw_time = row.get("completed_at") or row.get("at")
    try:
        at = datetime.fromisoformat(str(raw_time))
        if at.tzinfo is None:
            raise ValueError("timezone required")
        age = (now - at.astimezone(timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return {"plane": name, "status": "INVALID", "healthy": False,
                "reason": "HEARTBEAT_TIMESTAMP_INVALID", "heartbeat": row,
                "heartbeat_fresh": None, "pid_alive": None, "pid_identity": "UNKNOWN"}
    if age < 0:
        return {"plane": name, "status": "INVALID", "healthy": False,
                "reason": "HEARTBEAT_TIMESTAMP_IN_FUTURE", "heartbeat": row,
                "heartbeat_fresh": None, "pid_alive": None, "pid_identity": "UNKNOWN"}
    healthy_statuses = healthy_statuses or {"STARTED", "CYCLE_COMPLETED", "TICK_COMPLETED"}
    heartbeat_fresh = row.get("status") in healthy_statuses and age <= max_age_seconds
    pid = row.get("pid")
    pid_alive = process_identity.alive(pid)
    claimed_start = row.get("process_started_at")
    verified = (process_identity.verify_precise(pid, claimed_start)
                if pid_alive is True and isinstance(claimed_start, str) and claimed_start else None)
    parsed_start = parse_iso_or_none(claimed_start)
    if (verified is True and parsed_start is not None and parsed_start.tzinfo is not None
            and parsed_start.astimezone(timezone.utc) > at.astimezone(timezone.utc) + timedelta(milliseconds=1)):
        verified = False
    identity = "VERIFIED" if verified is True else "MISMATCH" if verified is False else "UNKNOWN"
    state = ("STALE_OR_FAILED" if not heartbeat_fresh else
             "DEAD_PROCESS" if pid_alive is False else
             "PID_MISMATCH" if identity == "MISMATCH" else
             "HEALTHY" if pid_alive is True and identity == "VERIFIED" else "PROCESS_UNKNOWN")
    return {"plane": name, "status": state, "healthy": state == "HEALTHY",
            "heartbeat_fresh": heartbeat_fresh, "pid_alive": pid_alive,
            "pid_identity": identity, "age_seconds": round(age, 3), "heartbeat": row}


def status(root: Path, *, max_age_seconds: float = 90.0,
           now: datetime | None = None) -> dict[str, Any]:
    """Read all resident-plane heartbeats without starting tasks or clients."""
    if (isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float))
            or not isfinite(max_age_seconds) or max_age_seconds <= 0):
        raise ValueError("max_age_seconds must be finite and positive")
    runtime = Path(root) / "runtime"
    # Read all records before sampling ``now``.  A writer can atomically
    # replace a heartbeat between a caller's first clock read and its file
    # read; sampling afterwards prevents that harmless microsecond race from
    # looking like a clock that moved into the future.  An explicit ``now`` is
    # retained unchanged for deterministic audit/test calls.
    core_row = _read(runtime / "aep-core-host.heartbeat.json")
    desktop_row = _read(runtime / "aep-supervisor.heartbeat.json")
    high_integrity_row = _read(runtime / "aep-elevated-worker.heartbeat.json")
    guardian_row = _read(runtime / "aep-desktop-guardian.heartbeat.json")
    now = now or datetime.now(timezone.utc)
    core = _plane("core", core_row,
                  now=now, max_age_seconds=float(max_age_seconds))
    desktop = _plane("desktop", desktop_row,
                     now=now, max_age_seconds=float(max_age_seconds))
    high_integrity = _plane("elevated", high_integrity_row,
                            now=now, max_age_seconds=float(max_age_seconds))
    guardian = _plane("desktop_guardian", guardian_row,
                      now=now, max_age_seconds=float(max_age_seconds),
                      healthy_statuses={"WORKER_STARTED", "HEALTHY"})
    # Elevated workers are one-shot by design: their absence is normal, unlike
    # core/desktop.  It becomes relevant only for a particular high-integrity
    # order and must not make the whole control plane look dead.
    required = (core, desktop)
    # A missing or contradictory capability declaration is not permission to
    # infer that the Desktop Host can type into a client.  In particular, an
    # old/failed heartbeat must not leave ``desktop_mode == dispatch`` visible
    # to a dashboard which does not also inspect ``healthy``.  Both fields are
    # emitted by ``supervisor._heartbeat`` and must agree before this read-only
    # status endpoint exposes a usable mode.
    desktop_heartbeat = desktop.get("heartbeat") or {}
    raw_mode = desktop_heartbeat.get("mode")
    raw_dispatch_enabled = desktop_heartbeat.get("dispatch_enabled")
    if not desktop["healthy"]:
        desktop_mode = "UNVERIFIED"
    elif raw_mode == "dispatch" and raw_dispatch_enabled is True:
        desktop_mode = "dispatch"
    elif raw_mode == "observe-only" and raw_dispatch_enabled is False:
        desktop_mode = "observe-only"
    else:
        desktop_mode = "UNVERIFIED"
    guardian_heartbeat = guardian.get("heartbeat") or {}
    if guardian["status"] == "MISSING":
        recovery_assurance = "LEGACY_UNSUPERVISED"
    elif not guardian["healthy"]:
        recovery_assurance = "GUARDIAN_UNHEALTHY"
    elif guardian_heartbeat.get("child_pid") != desktop_heartbeat.get("pid"):
        recovery_assurance = "GUARDIAN_CHILD_MISMATCH"
    elif os.name == "nt" and guardian_heartbeat.get("child_containment") != "WINDOWS_JOB":
        # On Windows, a Guardian that cannot prove Job containment could leave
        # a Desktop Host alive after its own failure.  Do not represent that as
        # supervised recovery merely because its heartbeat is fresh.
        recovery_assurance = "GUARDIAN_UNCONTAINED"
    else:
        recovery_assurance = "SUPERVISED"
    if not all(row["healthy"] for row in required):
        control_plane = "HOLD"
    elif recovery_assurance in {"GUARDIAN_UNHEALTHY", "GUARDIAN_CHILD_MISMATCH", "GUARDIAN_UNCONTAINED"}:
        # A guardian that claims a different child must not be ignored: the
        # desktop heartbeat could be a surviving pre-upgrade process.
        control_plane = "HOLD"
    elif desktop_mode == "observe-only":
        control_plane = "OBSERVE_ONLY"
    elif desktop_mode == "dispatch":
        control_plane = "READY"
    else:
        control_plane = "HOLD"
    return {
        "schema": "aep-resident-status-v1", "at": now.isoformat(),
        "coord_root": str(Path(root).resolve()), "max_age_seconds": max_age_seconds,
        "planes": {row["plane"]: row for row in (core, desktop, high_integrity, guardian)},
        "desktop_mode": desktop_mode,
        "recovery_assurance": recovery_assurance,
        "control_plane": control_plane,
        "delivery_claim": "NONE",
        "note": "READY requires fresh heartbeats and verified process identities; it does not prove a message was delivered or acknowledged.",
        "execution_started": False,
    }
