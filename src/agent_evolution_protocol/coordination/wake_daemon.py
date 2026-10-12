"""Event-driven wake daemon for the local multi-agent coordination bridge.

The daemon does not run a model.  It only consumes explicit wake_request files
created by `bridge.py wake`, calls the target adapter, and writes an audit log.
Use `--once` for tests or supervised runs; use `--watch` for a local background
worker with no model-token polling.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "wake_queue"
DONE = ROOT / "wake_done"
FAILED = ROOT / "wake_failed"
PENDING = ROOT / "wake_pending"
LOG = ROOT / "wake_log.jsonl"
ADAPTERS = ROOT / "wake_adapters"
COOLDOWN_SECONDS = 30


def stamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def adapter_report(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            return {key: item[key] for key in ("status", "method", "task_delivered", "agent_accepted") if key in item}
    return {}


def append_log(entry: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def claim_deadline(event: dict) -> str:
    seconds = event.get("claim_timeout_seconds", 600)
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = 600
    seconds = max(30, min(seconds, 86400))
    return datetime.fromtimestamp(time.time() + seconds, timezone.utc).astimezone().isoformat(timespec="seconds")


def mark_pending(event: dict, *, status: str) -> None:
    if event.get("dry_run"):
        return
    record = {
        **event,
        "adapter_status": status,
        "pending_since": stamp(),
        "claim_deadline": claim_deadline(event),
        "acceptance_required": "target agent must write claim or task-state for the same task",
    }
    write_json(PENDING / f"{event.get('id', 'unknown')}.json", record)


def move_atomic(src: Path, dst_dir: Path) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    os.replace(src, dst)
    return dst


def adapter_for(agent: str) -> Path:
    return ADAPTERS / f"{agent}.ps1"


def recent_success(agent: str, task: str, now: float) -> str | None:
    if not LOG.exists():
        return None
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()[-50:]
    except OSError:
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        # A different task for the same agent is new work, not a wake storm.
        # A focused window or invoked URI is not a delivered task.  Suppress
        # a new wake only after an adapter positively reports delivery.
        if (entry.get("to") != agent or entry.get("task") != task
                or entry.get("status") != "adapter-executed"
                or entry.get("adapter_report", {}).get("task_delivered") is not True):
            continue
        ts = entry.get("monotonic")
        if isinstance(ts, (int, float)) and now - ts < COOLDOWN_SECONDS:
            return str(entry.get("id", ""))
    return None


def consume(path: Path, *, dry_run: bool = False) -> int:
    now = time.monotonic()
    try:
        event = read_json(path)
    except Exception as exc:  # noqa: BLE001
        append_log(dict(at=stamp(), status="bad-event", path=str(path), error=str(exc), monotonic=now))
        move_atomic(path, FAILED)
        return 1

    if event.get("type") != "wake_request":
        append_log(dict(at=stamp(), status="ignored", id=event.get("id"), path=str(path), reason="not wake_request", monotonic=now))
        move_atomic(path, FAILED)
        return 1

    target = event.get("to", "")
    aid = event.get("id", path.stem)
    adapter = adapter_for(target)
    effective_dry_run = dry_run or bool(event.get("dry_run"))

    duplicate_of = recent_success(target, str(event.get("task", "")), now)
    if duplicate_of:
        append_log(dict(at=stamp(), status="duplicate-wake", id=aid, duplicate_of=duplicate_of, to=target, task=event.get("task"), monotonic=now))
        move_atomic(path, DONE)
        return 0

    if not adapter.exists():
        append_log(dict(at=stamp(), status="missing-adapter", id=aid, to=target, task=event.get("task"), adapter=str(adapter), monotonic=now))
        move_atomic(path, FAILED)
        return 2

    if effective_dry_run:
        append_log(dict(at=stamp(), status="dry-run", id=aid, to=target, task=event.get("task"), adapter=str(adapter), monotonic=now))
        move_atomic(path, DONE)
        return 0

    pwsh = shutil.which("pwsh.exe") or "pwsh.exe"
    cmd = [
        pwsh,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(adapter),
        "-WakeFile",
        str(path),
    ]
    try:
        completed = subprocess.run(cmd, cwd=str(ROOT.parent.parent), text=True, capture_output=True, timeout=60, check=False)
    except Exception as exc:  # noqa: BLE001
        append_log(dict(at=stamp(), status="adapter-error", id=aid, to=target, task=event.get("task"), error=str(exc), monotonic=now))
        move_atomic(path, FAILED)
        return 3

    report = adapter_report(completed.stdout)
    entry = dict(
        at=stamp(),
        status="adapter-executed" if completed.returncode == 0 else "adapter-failed",
        id=aid,
        to=target,
        task=event.get("task"),
        returncode=completed.returncode,
        stdout=completed.stdout[-2000:],
        stderr=completed.stderr[-2000:],
        adapter_report=report,
        monotonic=now,
    )
    append_log(entry)
    if completed.returncode == 0:
        mark_pending(event, status=str(report.get("status", "adapter-executed")))
    move_atomic(path, DONE if completed.returncode == 0 else FAILED)
    return 0 if completed.returncode == 0 else completed.returncode


def queue_items() -> list[Path]:
    QUEUE.mkdir(parents=True, exist_ok=True)
    return sorted(QUEUE.glob("*.json"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="consume current queue once and exit")
    mode.add_argument("--watch", action="store_true", help="watch queue until interrupted")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true", help="audit without executing adapters")
    args = parser.parse_args()

    if args.interval <= 0:
        parser.error("--interval must be positive")

    while True:
        rc = 0
        for item in queue_items():
            rc = max(rc, consume(item, dry_run=args.dry_run))
        if args.once:
            return rc
        time.sleep(args.interval)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
