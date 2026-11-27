"""Bounded per-user guardian for AEP's interactive Desktop Host.

Task Scheduler is a bootstrap mechanism, not the source of truth for process
recovery.  In particular, a scheduled task can remain ``Ready`` after an
unexpected worker exit without proving that a new interactive process was
started.  This module provides a small parent process which owns exactly one
Desktop Host child and makes its recovery decisions durable and inspectable.

It deliberately has a narrow authority boundary:

* it may start, stop and restart only the child ``supervisor`` process it
  created itself;
* it never enumerates or terminates processes by executable name, window name,
  or command-line substring;
* it writes a separate guardian heartbeat and a bounded restart ledger;
* a healthy child still proves only an interactive coordination loop, never a
  message delivery, an Agent ACK, or a task result.

The guardian is intended to be started by the existing per-user desktop task.
The SYSTEM Core Host remains durable-only and must never use this component to
touch a user interface.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable, Iterator, Protocol
from uuid import uuid4

from . import process_identity
from .foundation import read_json, write_json_atomic
from ..isotime import parse_iso_or_none


class AlreadyRunning(RuntimeError):
    """A guardian already owns this coordination root."""


class _Process(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> Any: ...

    def kill(self) -> Any: ...

    def wait(self, timeout: float | None = None) -> int: ...


class _Job(Protocol):
    name: str

    def assign_process(self, pid: int) -> Any: ...

    def close(self) -> Any: ...


Spawn = Callable[[list[str]], _Process]
Clock = Callable[[], datetime]
JobFactory = Callable[[str], _Job | None]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _runtime(root: Path) -> Path:
    path = Path(root) / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _guardian_heartbeat_path(root: Path) -> Path:
    return _runtime(root) / "aep-desktop-guardian.heartbeat.json"


def _guardian_state_path(root: Path) -> Path:
    return _runtime(root) / "aep-desktop-guardian.state.json"


@contextmanager
def _exclusive_lock(root: Path) -> Iterator[None]:
    """Hold a live guardian lock rather than trusting a stale PID file."""
    path = _runtime(root) / "aep-desktop-guardian.lock"
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - Windows is the intended deployment.
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunning("AEP Desktop Guardian already owns this coordination root") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _parse_records(value: object, *, now: datetime, window_seconds: float) -> list[datetime]:
    """Load and trim persisted restart times; malformed rows are discarded."""
    if not isinstance(value, list):
        return []
    threshold = now - timedelta(seconds=window_seconds)
    parsed: list[datetime] = []
    for item in value:
        stamp = parse_iso_or_none(item)
        if stamp is None:
            continue
        stamp = stamp.astimezone(timezone.utc)
        if threshold <= stamp <= now + timedelta(seconds=1):
            parsed.append(stamp)
    return sorted(parsed)


def _load_state(root: Path, *, now: datetime, window_seconds: float) -> dict[str, Any]:
    value = read_json(_guardian_state_path(root), default={})
    return value if isinstance(value, dict) else {}


def _restart_history(root: Path, *, now: datetime, window_seconds: float) -> list[datetime]:
    state = _load_state(root, now=now, window_seconds=window_seconds)
    return _parse_records(state.get("restart_times"), now=now, window_seconds=window_seconds)


def _next_restart(history: list[datetime], *, now: datetime, max_restarts: int,
                  window_seconds: float) -> datetime | None:
    if len(history) < max_restarts:
        return None
    return history[0] + timedelta(seconds=window_seconds)


def _state(root: Path, *, run_id: str, now: datetime, history: list[datetime],
           status: str, reason: str = "", child: _Process | None = None,
           child_started_at: str = "", next_restart_at: datetime | None = None,
           containment: str = "NOT_APPLICABLE", job_name: str = "") -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema": "aep-desktop-guardian-state-v1",
        "run_id": run_id,
        "updated_at": now.isoformat(),
        "status": status,
        "reason": reason,
        "restart_times": [item.isoformat() for item in history],
        "restart_count_window": len(history),
        "child_pid": child.pid if child is not None else None,
        "child_started_at": child_started_at,
        "next_restart_at": next_restart_at.isoformat() if next_restart_at else None,
        "child_containment": containment,
        "job_name": job_name,
    }
    write_json_atomic(_guardian_state_path(root), record, indent=2)
    return record


def _heartbeat(root: Path, *, run_id: str, now: datetime, status: str,
               mode: str, history: list[datetime], reason: str = "",
               child: _Process | None = None, child_started_at: str = "",
               next_restart_at: datetime | None = None,
               containment: str = "NOT_APPLICABLE", job_name: str = "") -> dict[str, Any]:
    """Write a guardian-only liveness record, never a delivery receipt."""
    record: dict[str, Any] = {
        "schema": "aep-desktop-guardian-heartbeat-v1",
        "at": now.isoformat(),
        "pid": os.getpid(),
        "process_started_at": process_identity.current_started_at(),
        "run_id": run_id,
        "status": status,
        "mode": mode,
        "reason": reason,
        "child_pid": child.pid if child is not None else None,
        "child_started_at": child_started_at,
        "restart_count_window": len(history),
        "next_restart_at": next_restart_at.isoformat() if next_restart_at else None,
        "child_containment": containment,
        "job_name": job_name,
        "ui_touched": False,
        "delivery_claim": "NONE",
    }
    write_json_atomic(_guardian_heartbeat_path(root), record, indent=2)
    return record


def _child_interpreter() -> str:
    """Prefer the real base interpreter over a Windows venv redirector.

    A ``Scripts\\python.exe`` virtual-environment redirector may create a new
    base-interpreter process and then exit.  ``Popen.pid`` would then describe
    the redirector rather than the Supervisor, defeating a guardian's direct
    child ownership.  CPython exposes its non-redirector executable through
    ``sys._base_executable``; when unavailable we retain the current
    interpreter and report normal heartbeat evidence instead of guessing.
    """
    candidate = getattr(sys, "_base_executable", "")
    if os.name == "nt" and isinstance(candidate, str) and Path(candidate).is_file():
        return candidate
    return sys.executable


def _child_environment() -> dict[str, str]:
    """Preserve this venv's packages when using its real base interpreter."""
    environment = dict(os.environ)
    paths = [item for item in environment.get("PYTHONPATH", "").split(os.pathsep) if item]
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        site_packages = Path(sys.prefix) / "Lib" / "site-packages"
        if site_packages.is_dir():
            paths.insert(0, str(site_packages))
    environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
    return environment


def _child_command(root: Path, *, interval_seconds: float, observe_only: bool,
                   no_perceive: bool, local_queue: bool = False) -> list[str]:
    command = [_child_interpreter(), "-m", "agent_evolution_protocol.coordination.supervisor",
               "--coord-root", str(Path(root).resolve()),
               "--interval-seconds", str(max(1.0, interval_seconds)),
               "--guardian-managed"]
    if observe_only:
        command.append("--observe-only")
    elif no_perceive:
        command.append("--no-perceive")
    if local_queue:
        command.append("--local-queue")
    return command


def _default_spawn(command: list[str]) -> _Process:
    """Start one known child; no shell and no inherited invisible launcher."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    return subprocess.Popen(command, shell=False, creationflags=flags,
                            env=_child_environment())  # noqa: S603


def _default_job_factory(name: str) -> _Job | None:
    """Create a Windows Job that dies with its Guardian parent.

    The non-Windows path exists only for portable unit tests.  A Windows
    deployment that cannot create the Job refuses to start instead of quietly
    losing parent-crash containment.
    """
    if os.name != "nt":
        return None
    from .windows_job import Job
    return Job.create(name)


def _read_child_heartbeat(root: Path, *, child: _Process,
                          now: datetime, stale_after_seconds: float,
                          startup_deadline: datetime) -> tuple[str, str]:
    """Classify a direct child's durable heartbeat without inferring delivery."""
    value = read_json(_runtime(root) / "aep-supervisor.heartbeat.json", default=None)
    if not isinstance(value, dict):
        return ("STARTING", "HEARTBEAT_MISSING") if now < startup_deadline else ("UNHEALTHY", "HEARTBEAT_MISSING")
    # ``child`` is a live Popen handle created by this guardian.  Matching its
    # PID is therefore a provenance check, unlike scanning an unrelated
    # machine-wide PID list.  The Supervisor's source remains backward
    # compatible: it need not know that a guardian was used to start it.
    if value.get("pid") != child.pid:
        return ("STARTING", "HEARTBEAT_NOT_THIS_CHILD") if now < startup_deadline else ("UNHEALTHY", "HEARTBEAT_NOT_THIS_CHILD")
    stamp = parse_iso_or_none(value.get("completed_at") or value.get("at"))
    if stamp is None or stamp.tzinfo is None:
        return ("UNHEALTHY", "HEARTBEAT_TIMESTAMP_INVALID")
    age = (now - stamp.astimezone(timezone.utc)).total_seconds()
    if age < -1:
        return ("UNHEALTHY", "HEARTBEAT_TIMESTAMP_FUTURE")
    if age > stale_after_seconds:
        return ("UNHEALTHY", "HEARTBEAT_STALE")
    if value.get("status") not in {"STARTED", "TICK_COMPLETED"}:
        return ("UNHEALTHY", "HEARTBEAT_FAILED")
    return "HEALTHY", ""


def _stop_owned_child(child: _Process) -> str:
    """Stop only a Popen child held by this guardian; never process-name kill."""
    if child.poll() is not None:
        return "ALREADY_EXITED"
    child.terminate()
    try:
        child.wait(timeout=5)
        return "TERMINATED"
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)
        return "KILLED_AFTER_TIMEOUT"


def watch(root: Path, *, interval_seconds: float = 5.0, stale_after_seconds: float = 90.0,
          startup_grace_seconds: float = 30.0, max_restarts: int = 5,
          restart_window_seconds: float = 600.0, observe_only: bool = False,
          no_perceive: bool = False, local_queue: bool = False, max_ticks: int = 0, spawn: Spawn = _default_spawn,
          sleep: Callable[[float], None] = time.sleep, clock: Clock = _utcnow,
          job_factory: JobFactory = _default_job_factory) -> int:
    """Supervise one restartable Desktop Host child with a persistent budget.

    ``max_ticks`` exists for deterministic smoke tests.  On a normal shutdown
    the guardian always terminates the child it owns, so it cannot leak a
    separate Python loop.  It never stops a pre-existing supervisor.
    """
    numeric = (interval_seconds, stale_after_seconds, startup_grace_seconds, restart_window_seconds)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0 for value in numeric):
        raise ValueError("interval, stale, grace and restart window must be positive")
    if isinstance(max_restarts, bool) or not isinstance(max_restarts, int) or max_restarts < 1:
        raise ValueError("max_restarts must be at least one")
    if isinstance(max_ticks, bool) or not isinstance(max_ticks, int) or max_ticks < 0:
        raise ValueError("max_ticks must be zero or positive")
    if local_queue and observe_only:
        raise ValueError("local_queue requires dispatch mode")
    root = Path(root)
    mode = "observe-only" if observe_only else "dispatch"
    run_id = uuid4().hex
    # Do not create the Job until this process owns the guardian lock.  A
    # second launcher must not create a needless named Job only to discover
    # that another Guardian already owns the coordination root.
    job: _Job | None = None
    containment = "PENDING" if os.name == "nt" else "NOT_APPLICABLE"
    job_name = ""
    child: _Process | None = None
    child_started_at = ""
    startup_deadline: datetime | None = None
    has_attempted = False
    ticks = 0
    with _exclusive_lock(root):
        history = _restart_history(root, now=clock(), window_seconds=float(restart_window_seconds))
        try:
            while True:
                now = clock().astimezone(timezone.utc)
                history = [item for item in history if item >= now - timedelta(seconds=restart_window_seconds)]
                next_restart = _next_restart(history, now=now, max_restarts=max_restarts,
                                             window_seconds=restart_window_seconds)
                status, reason = "STARTING", ""
                if child is None:
                    if next_restart is not None and now < next_restart:
                        status, reason = "RESTART_BACKOFF", "RESTART_BUDGET_EXHAUSTED"
                    else:
                        command = _child_command(root, interval_seconds=interval_seconds,
                                                 observe_only=observe_only,
                                                 no_perceive=no_perceive,
                                                 local_queue=local_queue)
                        # The first child is bootstrap, not a recovery.  Only
                        # subsequent starts consume the persisted restart budget.
                        if has_attempted:
                            history.append(now)
                        candidate: _Process | None = None
                        try:
                            # A real Windows Desktop Host is never started
                            # outside the Guardian's Job.  The Job's last
                            # handle belongs to this parent; therefore a
                            # Guardian crash reclaims the owned child tree
                            # instead of leaving a supervisor orphan behind.
                            if os.name == "nt" and job is None:
                                job = job_factory("AEPResident-" + uuid4().hex)
                                if job is None:
                                    raise OSError("Windows Job containment was not created")
                                containment = "WINDOWS_JOB"
                                job_name = job.name
                            candidate = spawn(command)
                            if job is not None:
                                job.assign_process(candidate.pid)
                        except (OSError, ValueError) as exc:
                            has_attempted = True
                            if candidate is not None:
                                _stop_owned_child(candidate)
                            if job is None and os.name == "nt":
                                containment = "UNAVAILABLE"
                                status = "CONTAINMENT_UNAVAILABLE"
                            else:
                                status = "WORKER_START_FAILED"
                            reason = f"{type(exc).__name__}: {exc}"[:500]
                        else:
                            child = candidate
                            has_attempted = True
                            child_started_at = now.isoformat()
                            startup_deadline = now + timedelta(seconds=startup_grace_seconds)
                            status, reason = "WORKER_STARTED", ""
                else:
                    code = child.poll()
                    if code is not None:
                        if code == 75:
                            status, reason = "WORKER_LOCK_HELD", "SUPERVISOR_ALREADY_RUNNING"
                        else:
                            status, reason = "WORKER_EXITED", f"EXIT_{code}"
                        child = None
                        child_started_at = ""
                        startup_deadline = None
                    else:
                        assert startup_deadline is not None
                        status, reason = _read_child_heartbeat(
                            root, child=child, now=now,
                            stale_after_seconds=stale_after_seconds,
                            startup_deadline=startup_deadline,
                        )
                        if status == "UNHEALTHY":
                            reason = f"{reason}:{_stop_owned_child(child)}"
                            child = None
                            child_started_at = ""
                            startup_deadline = None
                _state(root, run_id=run_id, now=now, history=history, status=status,
                       reason=reason, child=child, child_started_at=child_started_at,
                       next_restart_at=next_restart, containment=containment,
                       job_name=job_name)
                _heartbeat(root, run_id=run_id, now=now, status=status, mode=mode,
                           history=history, reason=reason, child=child,
                           child_started_at=child_started_at, next_restart_at=next_restart,
                           containment=containment, job_name=job_name)
                ticks += 1
                if max_ticks and ticks >= max_ticks:
                    return 0
                sleep(min(float(interval_seconds), 60.0))
        except KeyboardInterrupt:
            return 0
        finally:
            try:
                if child is not None:
                    stopped = _stop_owned_child(child)
                    now = clock().astimezone(timezone.utc)
                    _state(root, run_id=run_id, now=now, history=history, status="STOPPED",
                           reason=stopped, containment=containment, job_name=job_name)
                    _heartbeat(root, run_id=run_id, now=now, status="STOPPED", mode=mode,
                               history=history, reason=stopped, containment=containment,
                               job_name=job_name)
            finally:
                if job is not None:
                    job.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coord-root", type=Path, default=Path("coordination"))
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--stale-after-seconds", type=float, default=90.0)
    parser.add_argument("--startup-grace-seconds", type=float, default=30.0)
    parser.add_argument("--max-restarts", type=int, default=5)
    parser.add_argument("--restart-window-seconds", type=float, default=600.0)
    parser.add_argument("--observe-only", action="store_true")
    parser.add_argument("--no-perceive", action="store_true")
    parser.add_argument("--local-queue", action="store_true")
    parser.add_argument("--max-ticks", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        return watch(args.coord_root, interval_seconds=args.interval_seconds,
                     stale_after_seconds=args.stale_after_seconds,
                     startup_grace_seconds=args.startup_grace_seconds,
                     max_restarts=args.max_restarts,
                     restart_window_seconds=args.restart_window_seconds,
                     observe_only=args.observe_only, no_perceive=args.no_perceive,
                     local_queue=args.local_queue,
                     max_ticks=args.max_ticks)
    except AlreadyRunning as exc:
        print(json.dumps({"status": "ALREADY_RUNNING", "detail": str(exc)}, ensure_ascii=False), flush=True)
        return 0
    except ValueError as exc:
        parser.error(str(exc))
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
