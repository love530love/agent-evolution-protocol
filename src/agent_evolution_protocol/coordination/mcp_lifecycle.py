"""Forensic lifecycle evidence for AEP-owned MCP server instances.

An stdio MCP server belongs to the client that owns its stdin/stdout.  It is
therefore unsafe to globally deduplicate ``python``, ``node`` or ``conhost``
processes merely because their command lines look alike.  This registry records
only AEP federation MCP instances that explicitly opt in.  It never starts,
signals, kills, or otherwise changes a process.

Records are immutable *per run id*, not per PID.  Windows can reuse a PID after
a process exits; retaining a retired record makes that ambiguity visible rather
than silently classifying a later unrelated process as an old server.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any, Callable, Mapping
from uuid import uuid4

from agent_evolution_protocol.coordination.foundation import write_json_atomic


SCHEMA = "aep-mcp-instance-v1"
_AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_TRANSPORTS = {"stdio", "http", "sse", "ws"}
_STATES = {"RUNNING", "EXITED"}


def _now(moment: datetime | None = None) -> datetime:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _timestamp(moment: datetime | None = None) -> str:
    return _now(moment).isoformat()


def _agent(value: object) -> str:
    result = str(value or "").strip().lower()
    if not _AGENT.fullmatch(result):
        raise ValueError("agent must be a lowercase slug")
    return result


def _run_id(value: object) -> str:
    result = str(value or "").strip().lower()
    if not _RUN_ID.fullmatch(result):
        raise ValueError("invalid MCP lifecycle run_id")
    return result


def _positive_pid(value: object, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if result < 1:
        raise ValueError(f"{name} must be a positive integer")
    return result


def _parse_timestamp(value: object, *, name: str) -> datetime:
    text = str(value or "").strip()
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be ISO-8601 with timezone") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{name} must be ISO-8601 with timezone")
    return result.astimezone(timezone.utc)


def _command_digest(argv: list[str] | None = None) -> str:
    values = argv if argv is not None else list(sys.argv)
    text = "\0".join(str(item) for item in values)
    return "sha256:" + sha256(text.encode("utf-8", errors="replace")).hexdigest()


def directory(root: str | Path) -> Path:
    return Path(root) / "mcp_instances"


def path(root: str | Path, run_id: str) -> Path:
    return directory(root) / f"{_run_id(run_id)}.json"


def _valid(row: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(row, Mapping) or row.get("schema") != SCHEMA:
        raise ValueError("invalid MCP lifecycle schema")
    transport = str(row.get("transport", "")).strip().lower()
    if transport not in _TRANSPORTS:
        raise ValueError("invalid MCP lifecycle transport")
    digest = str(row.get("command_sha256", "")).strip().casefold()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("invalid MCP lifecycle command_sha256")
    workspace = row.get("workspace_id", "")
    if not isinstance(workspace, str) or "\0" in workspace:
        raise ValueError("invalid MCP lifecycle workspace_id")
    server = row.get("server", "")
    if not isinstance(server, str) or not server.strip() or "\0" in server:
        raise ValueError("invalid MCP lifecycle server")
    state = str(row.get("state", "")).strip().upper()
    if state not in _STATES:
        raise ValueError("invalid MCP lifecycle state")
    result = {
        "schema": SCHEMA,
        "run_id": _run_id(row.get("run_id")),
        "pid": _positive_pid(row.get("pid"), name="pid"),
        "parent_pid": _positive_pid(row.get("parent_pid"), name="parent_pid"),
        "agent": _agent(row.get("agent")),
        "workspace_id": workspace.strip(),
        "server": server.strip(),
        "transport": transport,
        "started_at": _parse_timestamp(row.get("started_at"), name="started_at").isoformat(),
        "heartbeat_at": _parse_timestamp(row.get("heartbeat_at"), name="heartbeat_at").isoformat(),
        "command_sha256": digest,
        "state": state,
    }
    stopped = row.get("stopped_at")
    if state == "EXITED":
        result["stopped_at"] = _parse_timestamp(stopped, name="stopped_at").isoformat()
    elif stopped not in (None, ""):
        raise ValueError("running MCP lifecycle record cannot have stopped_at")
    return result


def _load(root: str | Path, run_id: str) -> dict[str, Any] | None:
    try:
        return _valid(json.loads(path(root, run_id).read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def register(root: str | Path, *, agent: str, workspace_id: str = "",
             server: str = "aep-federation", transport: str = "stdio",
             pid: int | None = None, parent_pid: int | None = None,
             argv: list[str] | None = None, moment: datetime | None = None) -> dict[str, Any]:
    """Record one AEP MCP process without retaining command-line contents."""
    current_pid = _positive_pid(os.getpid() if pid is None else pid, name="pid")
    current_parent = _positive_pid(os.getppid() if parent_pid is None else parent_pid,
                                   name="parent_pid")
    now = _timestamp(moment)
    row = _valid({
        "schema": SCHEMA,
        "run_id": uuid4().hex,
        "pid": current_pid,
        "parent_pid": current_parent,
        "agent": agent,
        "workspace_id": workspace_id,
        "server": server,
        "transport": transport,
        "started_at": now,
        "heartbeat_at": now,
        "command_sha256": _command_digest(argv),
        "state": "RUNNING",
    })
    write_json_atomic(path(root, row["run_id"]), row, indent=2)
    return row


def heartbeat(root: str | Path, *, pid: int, run_id: str,
              moment: datetime | None = None) -> dict[str, Any] | None:
    """Refresh only the exact, still-running AEP instance."""
    row = _load(root, run_id)
    if row is None or row["pid"] != _positive_pid(pid, name="pid") or row["state"] != "RUNNING":
        return None
    row["heartbeat_at"] = _timestamp(moment)
    write_json_atomic(path(root, row["run_id"]), row, indent=2)
    return row


def retire(root: str | Path, *, pid: int, run_id: str,
           moment: datetime | None = None) -> dict[str, Any] | None:
    """Retire only the exiting process's exact record; keep forensic evidence."""
    row = _load(root, run_id)
    if row is None or row["pid"] != _positive_pid(pid, name="pid"):
        return None
    if row["state"] == "EXITED":
        return row
    row["state"] = "EXITED"
    row["heartbeat_at"] = _timestamp(moment)
    row["stopped_at"] = row["heartbeat_at"]
    row = _valid(row)
    write_json_atomic(path(root, row["run_id"]), row, indent=2)
    return row


def unregister(root: str | Path, *, pid: int, run_id: str) -> bool:
    """Compatibility alias: retirement is auditable and no longer deletes evidence."""
    return retire(root, pid=pid, run_id=run_id) is not None


def pid_state(pid: int) -> str:
    """Return ``live``, ``dead`` or ``unknown`` without WMI.

    Permission errors deliberately stay unknown.  A registry sweep must not
    infer that an inaccessible process is safe to remove.
    """
    try:
        os.kill(_positive_pid(pid, name="pid"), 0)
    except ProcessLookupError:
        return "dead"
    except PermissionError:
        return "unknown"
    except OSError:
        return "unknown"
    return "live"


def _status(record: dict[str, Any], *, probe: Callable[[int], str], now: datetime,
            stale_after_seconds: int) -> dict[str, Any]:
    heartbeat_at = _parse_timestamp(record["heartbeat_at"], name="heartbeat_at")
    age = (now - heartbeat_at).total_seconds()
    if record["state"] == "EXITED":
        pid = "not_probed"
        status = "EXITED"
    else:
        pid = str(probe(record["pid"])).strip().lower()
        if pid not in {"live", "dead", "unknown"}:
            pid = "unknown"
        status = ("DEAD_PID" if pid == "dead" else "UNKNOWN_PID" if pid == "unknown"
                  else "HEARTBEAT_FUTURE" if age < 0
                  else "HEARTBEAT_STALE" if age > stale_after_seconds else "LIVE")
    return {**record, "pid_state": pid, "heartbeat_age_seconds": round(age, 3), "status": status}


def snapshot(root: str | Path, *, probe: Callable[[int], str] = pid_state,
             moment: datetime | None = None, stale_after_seconds: int = 120) -> dict[str, Any]:
    """Inspect AEP-owned records only; this never enumerates or changes processes."""
    if isinstance(stale_after_seconds, bool) or stale_after_seconds < 1:
        raise ValueError("stale_after_seconds must be positive")
    now = _now(moment)
    rows: list[dict[str, Any]] = []
    folder = directory(root)
    for item in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            record = _valid(json.loads(item.read_text(encoding="utf-8")))
            rows.append({**_status(record, probe=probe, now=now,
                                   stale_after_seconds=stale_after_seconds),
                         "record_path": str(item)})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            rows.append({"record_path": str(item), "status": "INVALID_RECORD", "error": str(exc)})
    labels = ("LIVE", "HEARTBEAT_FUTURE", "HEARTBEAT_STALE", "DEAD_PID", "UNKNOWN_PID", "EXITED", "INVALID_RECORD")
    counts = {label: sum(1 for row in rows if row["status"] == label) for label in labels}
    return {
        "schema": "aep-mcp-lifecycle-status-v1",
        "at": now.isoformat(),
        "instances": rows,
        "counts": counts,
        "processes_terminated": 0,
        "note": "AEP observes only its own registered MCP records; no process was started or terminated.",
    }


def prune(root: str | Path, *, probe: Callable[[int], str] = pid_state,
          moment: datetime | None = None, stale_after_seconds: int = 120,
          retention_seconds: int = 86_400) -> dict[str, Any]:
    """Remove only old retired/dead *record files*, never processes or stale records."""
    if isinstance(retention_seconds, bool) or retention_seconds < 1:
        raise ValueError("retention_seconds must be positive")
    report = snapshot(root, probe=probe, moment=moment,
                      stale_after_seconds=stale_after_seconds)
    now = _now(moment)
    removed: list[str] = []
    retained: list[str] = []
    for row in report["instances"]:
        target = Path(row["record_path"])
        status = row.get("status")
        if status not in {"EXITED", "DEAD_PID"}:
            retained.append(str(target))
            continue
        stamp = row.get("stopped_at") if status == "EXITED" else row.get("heartbeat_at")
        try:
            age = max(0.0, (now - _parse_timestamp(stamp, name="retention timestamp")).total_seconds())
        except ValueError:
            retained.append(str(target))
            continue
        if age < retention_seconds:
            retained.append(str(target))
            continue
        try:
            target.unlink(missing_ok=True)
            removed.append(str(target))
        except OSError:
            retained.append(str(target))
    return {
        **report,
        "removed_records": removed,
        "retained_records": retained,
        "retention_seconds": retention_seconds,
        "processes_terminated": 0,
        "note": "Only aged EXITED/DEAD_PID AEP record files were removed; no MCP, shell, or client process was terminated.",
    }


def start_heartbeat(root: str | Path, record: Mapping[str, Any], *,
                    interval_seconds: float = 30.0) -> tuple[threading.Event, threading.Thread]:
    """Start a daemon heartbeat thread for one registered server instance."""
    row = _valid(record)
    interval = max(5.0, min(float(interval_seconds), 300.0))
    stopped = threading.Event()

    def run() -> None:
        while not stopped.wait(interval):
            if heartbeat(root, pid=row["pid"], run_id=row["run_id"]) is None:
                return

    worker = threading.Thread(target=run, name="aep-mcp-heartbeat", daemon=True)
    worker.start()
    return stopped, worker
