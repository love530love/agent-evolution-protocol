"""Non-interactive durable control plane for a Windows AEP deployment.

``core_host`` is deliberately not a desktop automation loop.  Windows services
run in Session 0 and cannot reliably see a logged-in user's Qoder, WorkBuddy or
CodeBuddy windows.  This host therefore owns only durable, non-UI work:

* reconcile presence, mailbox and deadlines;
* persist verified mission orders as ``DEPOSITED``;
* advance the federation DAG with ``dispatch=False``;
* publish a small heartbeat and enforce one owner per coordination root.

An interactive :mod:`supervisor` (the *Desktop Host*) is the only resident
component allowed to call ``commander.tick`` and touch a window.  Separating the
two makes an unattended Core Host useful without turning a healthy Windows
service into a false claim that a message was typed or acknowledged.
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

from . import campaign_tick, mission_pulse
from .foundation import write_json_atomic


class AlreadyRunning(RuntimeError):
    """A Core Host already owns the requested coordination root."""


def _heartbeat_path(root: Path) -> Path:
    return Path(root) / "runtime" / "aep-core-host.heartbeat.json"


def _heartbeat(root: Path, payload: dict) -> None:
    write_json_atomic(_heartbeat_path(root), {
        "schema": "aep-core-host-heartbeat-v1", "pid": os.getpid(),
        "ui_touched": False, **payload,
    }, indent=2)


@contextmanager
def _exclusive_lock(root: Path) -> Iterator[None]:
    """Use a real held file lock, never a stale-PID heuristic."""
    path = Path(root) / "runtime" / "aep-core-host.lock"
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
            else:  # pragma: no cover - Windows is the deployment target.
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunning("AEP Core Host already owns this coordination root") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - Windows is the deployment target.
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def cycle(coord_root: Path, *, bridge_root: Path, kernel_root: Path | None = None,
          workspace_id: str = "", agents: list[str] | None = None,
          sender: str = "core-host") -> dict:
    """Perform one durable-only pass.  It has no desktop dispatch path."""
    coord_root = Path(coord_root)
    names = [str(item).strip() for item in (agents or []) if str(item).strip()]
    supplied = (kernel_root is not None, bool(workspace_id), bool(names))
    if any(supplied) and not all(supplied):
        raise ValueError("kernel_root, workspace_id and agents must be supplied together")
    result: dict = {
        "ui_touched": False, "dispatch_performed": False,
        "campaign": campaign_tick.tick(coord_root, Path(bridge_root)),
        "missions": mission_pulse.pulse(coord_root, apply=True, sender=sender),
        "federation": {"status": "SKIPPED", "reason": "FEDERATION_CONTEXT_MISSING"},
    }
    if all(supplied):
        from agent_evolution_protocol.federation import supervisor as federation_supervisor

        adapters = federation_supervisor.build_adapters(
            coord_root=str(coord_root), agents=names, workspace_id=workspace_id,
        )
        result["federation"] = federation_supervisor.step(
            Path(kernel_root), coord_root, workspace_id, adapters,
            dispatch=False, perceive_first=False,
        )
        # This is a contract assertion: changing federation.step() must not
        # quietly grant a Core Host a hand on the desktop.
        if result["federation"].get("sent"):
            raise RuntimeError("CORE_HOST_DISPATCH_VIOLATION")
    return result


def watch(coord_root: Path, *, bridge_root: Path, kernel_root: Path | None = None,
          workspace_id: str = "", agents: list[str] | None = None,
          sender: str = "core-host", interval_seconds: float = 30.0,
          max_ticks: int = 0, stream: TextIO | None = None,
          sleep=time.sleep) -> int:
    """Run durable-only cycles with one lock and one heartbeat record."""
    if interval_seconds < 1:
        raise ValueError("interval_seconds must be at least one second")
    if max_ticks < 0:
        raise ValueError("max_ticks must be zero (unbounded) or positive")
    output = stream or sys.stdout
    coord_root = Path(coord_root)
    with _exclusive_lock(coord_root):
        started = datetime.now(timezone.utc).isoformat()
        _heartbeat(coord_root, {"status": "STARTED", "at": started, "tick": 0,
                                "coord_root": str(coord_root.resolve()),
                                "desktop_dispatch_allowed": False})
        print(json.dumps({"status": "STARTED", "pid": os.getpid(), "at": started,
                          "desktop_dispatch_allowed": False}, ensure_ascii=False),
              file=output, flush=True)
        count = 0
        while True:
            started_at = datetime.now(timezone.utc).isoformat()
            began = time.monotonic()
            try:
                result = cycle(coord_root, bridge_root=bridge_root, kernel_root=kernel_root,
                               workspace_id=workspace_id, agents=agents, sender=sender)
                count += 1
                payload = {"status": "CYCLE_COMPLETED", "tick": count,
                           "started_at": started_at,
                           "completed_at": datetime.now(timezone.utc).isoformat(),
                           "duration_seconds": round(time.monotonic() - began, 3),
                           "queued_orders": len(result["missions"].get("queued") or []),
                           "federation": result["federation"].get("status", "STEPPED")}
            except Exception as exc:  # retain liveness evidence rather than dying silently
                count += 1
                payload = {"status": "CYCLE_FAILED", "tick": count, "started_at": started_at,
                           "completed_at": datetime.now(timezone.utc).isoformat(),
                           "duration_seconds": round(time.monotonic() - began, 3),
                           "error": f"{type(exc).__name__}: {exc}"[:1000]}
            _heartbeat(coord_root, payload)
            print(json.dumps(payload, ensure_ascii=False), file=output, flush=True)
            if max_ticks and count >= max_ticks:
                return 0
            sleep(interval_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coord-root", type=Path, default=Path("coordination"))
    parser.add_argument("--bridge-root", type=Path, default=Path("coordination"))
    parser.add_argument("--kernel-root", type=Path, default=None)
    parser.add_argument("--workspace", default="")
    parser.add_argument("--agents", default="")
    parser.add_argument("--sender", default="core-host")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--max-ticks", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        return watch(args.coord_root, bridge_root=args.bridge_root, kernel_root=args.kernel_root,
                     workspace_id=args.workspace,
                     agents=[row.strip() for row in args.agents.split(",") if row.strip()],
                     sender=args.sender, interval_seconds=args.interval_seconds,
                     max_ticks=args.max_ticks)
    except AlreadyRunning as exc:
        print(json.dumps({"status": "ALREADY_RUNNING", "detail": str(exc)}, ensure_ascii=False))
        return 0
    except ValueError as exc:
        parser.error(str(exc))
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
