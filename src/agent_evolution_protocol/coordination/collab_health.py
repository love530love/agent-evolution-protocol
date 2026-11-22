"""Read-only, evidence-graded collaboration health snapshot.

One command turns the four online layers, message delivery and the resident
hosts into a recomputable JSON report for unattended watch.  Evidence levels
are never promoted: a heartbeat pid is not proof of a live OS process, mailbox
DEPOSITED and MESSAGE_VISIBLE are not Agent ACK, and an expired presence row is
stale -- never offline.  Layers without a reliable read-only source are
reported as ``"unknown"`` instead of being filled with a guess.

This module performs no writes, no subprocesses, no network and no model calls.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

from ..isotime import parse_iso_or_none
from . import process_identity
from .delivery_audit import audit as _audit_delivery

__all__ = ["snapshot", "main"]

# Heartbeats older than this are proven stale, not merely "not seen".
HOST_MAX_AGE_SECONDS = 90.0

_AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_HOST_FILES = {
    "core": "aep-core-host.heartbeat.json",
    "desktop": "aep-supervisor.heartbeat.json",
}
_HOST_OK_STATES = {"STARTED", "CYCLE_COMPLETED", "TICK_COMPLETED"}

_UNKNOWN_NOTE = (
    "No reliable read-only source: a pid in a heartbeat is not proof of a "
    "running OS process, and no window handle is enumerated here."
)
_DELIVERY_NOTE = "Mailbox DEPOSITED and MESSAGE_VISIBLE are not Agent ACK."


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _as_utc(moment: datetime) -> datetime:
    return (moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None
            else moment.astimezone(timezone.utc))


def _resolve_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime):
        raise TypeError("now must be a datetime or None")
    return _as_utc(now)


def _presence_layers(root: Path, at: datetime) -> tuple[int, int, int]:
    """Return (file-registered online, effective fresh, expired/stale) counts."""
    registered = fresh = stale = 0
    for path in sorted((root / "presence" / "agents").glob("*.json")):
        row = _read_json(path)
        if not row:
            continue
        agent = row.get("agent")
        if not isinstance(agent, str) or not _AGENT.fullmatch(agent):
            continue
        if row.get("state") != "online":
            continue
        registered += 1
        expiry = parse_iso_or_none(row.get("expires_at"), assume_utc=True)
        if expiry is not None and expiry > at:
            fresh += 1
        else:
            # Expired evidence is stale, never offline.
            stale += 1
    return registered, fresh, stale


def _pid_alive(pid) -> bool | None:
    """Read-only OS liveness check for a recorded heartbeat PID.

    A fresh heartbeat *file* with a dead PID is a stale process that stopped
    updating it.  Return ``None`` when the platform or permissions cannot
    answer, never ``True`` by default.  No process is signalled or started.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if os.name == "posix":
        try:
            os.kill(pid, 0)  # Existence probe; signal 0 does not signal the process.
            return True
        except ProcessLookupError:
            return False
        except (PermissionError, OSError):
            return None
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            kernel32.GetExitCodeProcess.restype = ctypes.c_bool
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return None


def _host(path: Path, at: datetime) -> dict:
    row = _read_json(path)
    if row is None:
        return {"tick": "unknown", "at": "unknown", "fresh": "unknown",
                "health": "UNKNOWN", "pid_alive": "unknown"}
    raw = row.get("completed_at") or row.get("at")
    stamp = parse_iso_or_none(raw, assume_utc=True) if isinstance(raw, str) else None
    raw_tick = row.get("tick")
    tick = raw_tick if isinstance(raw_tick, int) and not isinstance(raw_tick, bool) else "unknown"
    raw_pid = row.get("pid")
    pid = raw_pid if isinstance(raw_pid, int) and not isinstance(raw_pid, bool) else 0
    pid_alive = _pid_alive(pid)
    claimed_start = row.get("process_started_at", "")
    if pid_alive and isinstance(claimed_start, str) and claimed_start:
        verified = process_identity.verify(pid, claimed_start)
        pid_identity = "VERIFIED" if verified is True else "MISMATCH" if verified is False else "UNKNOWN"
    else:
        pid_identity = "MISSING" if pid_alive else "UNKNOWN"
    if stamp is None:
        return {"tick": tick, "at": "unknown", "fresh": "unknown",
                "health": "UNKNOWN", "pid": pid, "pid_alive": pid_alive,
                "pid_identity": pid_identity, "clock_skew_suspect": False}
    age = (at - _as_utc(stamp)).total_seconds()
    if age < 0:
        return {"tick": tick, "at": stamp.isoformat(), "fresh": "unknown",
                "health": "UNKNOWN", "pid": pid, "pid_alive": pid_alive,
                "pid_identity": pid_identity, "clock_skew_suspect": True}
    fresh = age <= HOST_MAX_AGE_SECONDS and row.get("status") in _HOST_OK_STATES
    # A fresh file is not a live host: a dead PID or a PID whose recorded start
    # time no longer matches means another process inherited the number.  A
    # legacy heartbeat with no start-time token keeps the old liveness-only
    # behaviour but is explicitly marked MISSING rather than claimed verified.
    health = ("DEAD_PROCESS" if fresh and pid_alive is False else
              "PID_MISMATCH" if fresh and pid_identity == "MISMATCH" else
              "FRESH" if fresh else "STALE_OR_FAILED")
    return {"tick": tick, "at": stamp.isoformat(),
            "fresh": bool(fresh and pid_alive is not False and pid_identity != "MISMATCH"),
            "health": health, "pid": pid, "pid_alive": pid_alive,
            "pid_identity": pid_identity, "age_seconds": round(age, 3),
            "clock_skew_suspect": False}


def _verdict(online_layers: dict, hosts: dict) -> str:
    """Fixed priority: no fresh agent > proven stale/failed host > missing host > READY."""
    if online_layers["presence_effective_fresh"] == 0:
        return "NO_ONLINE_AGENT"
    if any(host["fresh"] is False for host in hosts.values()):
        return "HOSTS_STALE"
    if any(host["fresh"] == "unknown" for host in hosts.values()):
        return "HOSTS_UNKNOWN"
    return "READY"


def snapshot(coord_root, *, now: datetime | None = None) -> dict:
    """Build a deterministic, read-only collaboration health report.

    ``coord_root`` is the coordination directory (``coordination/``).  ``now``
    is the single evaluation instant for every freshness check; pass it to make
    the result fully reproducible.
    """
    root = Path(coord_root)
    at = _resolve_now(now)
    registered, fresh, stale = _presence_layers(root, at)
    online_layers = {
        "file_registered_online": registered,
        "presence_effective_fresh": fresh,
        "presence_stale": stale,
        "os_processes_running": "unknown",
        "windows_actionable": "unknown",
        "unknown_note": _UNKNOWN_NOTE,
    }
    delivery_raw = _audit_delivery(root, now=at)
    delivery = {
        "next_required": delivery_raw["next_required"],
        "agent_accepted": delivery_raw["agent_accepted"],
        "message_visible": delivery_raw["message_visible"],
        "messages": delivery_raw["messages"],
        "clock_skew_suspect": delivery_raw.get("clock_skew_suspect", False),
        "note": _DELIVERY_NOTE,
    }
    runtime = root / "runtime"
    hosts = {name: _host(runtime / filename, at)
             for name, filename in _HOST_FILES.items()}
    return {
        "schema": "aep-collab-health-v1",
        "at": at.isoformat(),
        "coord_root": str(root),
        "online_layers": online_layers,
        "delivery": delivery,
        "hosts": hosts,
        "clock_skew_suspect": (delivery["clock_skew_suspect"] or
                               any(host.get("clock_skew_suspect") for host in hosts.values())),
        "verdict": _verdict(online_layers, hosts),
        "note": "READY means fresh online evidence and healthy hosts; it is not a delivery or ACK claim.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd() / "coordination",
                        help="coordination directory to inspect (read-only)")
    args = parser.parse_args(argv)
    print(json.dumps(snapshot(args.root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
