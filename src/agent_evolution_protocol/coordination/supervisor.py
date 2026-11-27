"""Single-instance Python control loop for durable commander supervision.

PowerShell remains useful for Task Scheduler registration and Windows-specific
interactive operations.  The long-running scheduling loop lives here so one
well-defined Python process owns tick cadence, heartbeats, exit codes and the
single-instance lock instead of spawning a fresh CLI process every interval.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Iterator, TextIO

from . import commander, process_identity, unattended_queue
from .foundation import write_json_atomic


class AlreadyRunning(RuntimeError):
    """The coordination root already has a live supervisor lock holder."""


def _heartbeat_path(root: Path) -> Path:
    return Path(root) / "runtime" / "aep-supervisor.heartbeat.json"


def _heartbeat(root: Path, payload: dict) -> dict:
    """Persist a small health record without claiming an order was accepted."""
    record = {
        "schema": "aep-supervisor-heartbeat-v1",
        "pid": os.getpid(),
        "process_started_at": process_identity.current_started_at(),
        **payload,
    }
    # A resident tick can prove only that the loop is alive.  Even a nonempty
    # dispatched list records attempts, never delivery or acceptance.
    record["delivery_claim"] = "NONE"
    record["dispatch_enabled"] = record.get("mode") == "dispatch"
    write_json_atomic(_heartbeat_path(root), record, indent=2)
    return record


@contextmanager
def _exclusive_lock(root: Path) -> Iterator[None]:
    path = Path(root) / "runtime" / "aep-supervisor.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
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
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunning("AEP supervisor already owns this coordination root") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def watch(root: Path, *, interval_seconds: float = 30.0, max_ticks: int = 0,
          perceive_first: bool = True, dispatch: bool = True,
          local_queue: bool = False, stream: TextIO | None = None) -> int:
    """Run the interactive Desktop Host, optionally in receipt-only mode.

    ``dispatch=False`` is deliberately narrower than a dry run: it reads
    already-arrived ACK evidence and updates only the associated durable order
    state.  It never focuses a window, discovers a composer or types text.  It
    is the safe way to prove that the logged-in host can stay alive before a
    separately approved order is allowed to use its desktop hand.

    ``local_queue=True`` makes the same resident loop drain
    ``coord-unattended``: when a dispatch tick handed nothing to an agent, at
    most one queued local task runs before the next heartbeat.  It is
    fallback capacity, not a competing route -- an explicit local plan is
    executed exactly as stored, and its process/output receipt is never
    reported as an order delivery or an agent ACK.
    """
    if interval_seconds < 1:
        raise ValueError("interval_seconds must be at least one second")
    if max_ticks < 0:
        raise ValueError("max_ticks must be zero (unbounded) or positive")
    output = stream or sys.stdout
    with _exclusive_lock(Path(root)):
        root = Path(root)
        started = datetime.now(timezone.utc).isoformat()
        started_payload = {"status": "STARTED", "at": started,
                           "coord_root": str(root.resolve()),
                           "interval_seconds": interval_seconds,
                           "perceive_first": perceive_first, "tick": 0,
                           "local_queue_enabled": bool(local_queue and dispatch),
                           "mode": "dispatch" if dispatch else "observe-only"}
        started_record = _heartbeat(root, started_payload)
        print(json.dumps(started_record, ensure_ascii=False),
              file=output, flush=True)
        count = 0
        while True:
            tick_started = time.monotonic()
            at = datetime.now(timezone.utc).isoformat()
            try:
                result = (commander.tick(root, perceive_first=perceive_first) if dispatch
                          else {"at": at, "perceived": {"skipped": True},
                                "acks": commander.check_acks(root), "dispatched": []})
                count += 1
                local = None
                if local_queue and dispatch and not (result.get("dispatched") or []):
                    # Nothing was handed to an agent this tick, so the queue's
                    # explicit local plan is the only work that can move.  A
                    # broken queue must not take the heartbeat down with it.
                    try:
                        ran = unattended_queue.run_once(root)
                    except (OSError, ValueError) as exc:
                        ran = {"state": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
                    local = {"state": ran.get("state", ""),
                             "task_id": ran.get("task_id", ""),
                             "error": ran.get("error", "")}
                payload = {
                    "status": "TICK_COMPLETED", "at": result.get("at", at), "tick": count,
                    "started_at": at, "completed_at": datetime.now(timezone.utc).isoformat(),
                    "duration_seconds": round(time.monotonic() - tick_started, 3),
                    "dispatched": result.get("dispatched") or [],
                    "perceived": bool(result.get("perceived") and not result["perceived"].get("skipped")),
                    "local_queue_enabled": bool(local_queue and dispatch),
                    "mode": "dispatch" if dispatch else "observe-only",
                }
                if local is not None:
                    payload["local"] = local
                record = _heartbeat(root, payload)
                print(json.dumps(record, ensure_ascii=False), file=output, flush=True)
            except Exception as exc:
                count += 1
                payload = {
                    "status": "TICK_FAILED", "at": datetime.now(timezone.utc).isoformat(), "tick": count,
                    "started_at": at, "completed_at": datetime.now(timezone.utc).isoformat(),
                    "duration_seconds": round(time.monotonic() - tick_started, 3),
                    "error": f"{type(exc).__name__}: {exc}"[:1000],
                    "local_queue_enabled": bool(local_queue and dispatch),
                    "mode": "dispatch" if dispatch else "observe-only",
                }
                record = _heartbeat(root, payload)
                print(json.dumps(record, ensure_ascii=False), file=output, flush=True)
            if max_ticks and count >= max_ticks:
                return 0
            time.sleep(interval_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coord-root", type=Path, default=Path(".agent-evolution/coordination"))
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--max-ticks", type=int, default=0)
    parser.add_argument("--no-perceive", action="store_true")
    parser.add_argument("--observe-only", action="store_true",
                        help="read same-event ACK evidence only; never touch a window or dispatch")
    parser.add_argument("--guardian-managed", action="store_true",
                        help="use a distinct exit code when the Desktop Guardian cannot acquire this host lock")
    parser.add_argument("--local-queue", action="store_true",
                        help="run at most one coord-unattended task when a tick dispatched nothing")
    args = parser.parse_args(argv)
    try:
        return watch(args.coord_root, interval_seconds=args.interval_seconds,
                     max_ticks=args.max_ticks, perceive_first=not args.no_perceive,
                     dispatch=not args.observe_only, local_queue=args.local_queue)
    except AlreadyRunning as exc:
        print(json.dumps({"status": "ALREADY_RUNNING", "coord_root": str(args.coord_root.resolve()),
                          "detail": str(exc)}, ensure_ascii=False), flush=True)
        # A manually launched legacy host keeps the historic benign result.
        # Guardian must distinguish that condition from a healthy child that
        # chose to finish: otherwise an orphaned pre-upgrade host looks like a
        # sequence of ordinary EXIT_0 recoveries and obscures the real lock
        # owner.  75 is the conventional temporary-failure status.
        return 75 if args.guardian_managed else 0
    except ValueError as exc:
        parser.error(str(exc))
    return 2  # pragma: no cover - argparse raises before this line


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
