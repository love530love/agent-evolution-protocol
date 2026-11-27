"""Durable local work for the hours when no agent is online.

``external_runner`` can execute one bounded command and ``federation`` can
assign one task to a live agent, but neither keeps a queue for the case both
facts are false.  This module closes that gap without inventing a second
scheduler: a task is an immutable, hash-pinned manifest, one tick executes at
most one ready task, and every receipt is process/file evidence -- never an
agent ACK.  A failed or uncertain command becomes terminal until an operator
explicitly requeues it, because a process that already ran may have written
half of its output.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from agent_evolution_protocol.federation import assignment
from agent_evolution_protocol.federation.locking import LockTimeout, workspace_lock

from . import external_runner, presence


TASK_SCHEMA = "aep-unattended-task-v1"
RECORD_SCHEMA = "aep-unattended-record-v1"
RECEIPT_SCHEMA = "aep-unattended-receipt-v1"
STATUS_SCHEMA = "aep-unattended-status-v1"

TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
STATES = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "UNKNOWN", "BLOCKED", "CANCELLED")
TERMINAL = frozenset({"SUCCEEDED", "FAILED", "UNKNOWN", "BLOCKED", "CANCELLED"})
MAX_TIMEOUT_SECONDS = 24 * 60 * 60
MAX_TAIL_CHARS = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not contain an object")
    return value


def _queue_root(root: Path) -> Path:
    return Path(root) / "unattended"


def _tasks(root: Path) -> Path:
    return _queue_root(root) / "tasks"


def _receipts(root: Path) -> Path:
    return _queue_root(root) / "receipts"


def _runs(root: Path) -> Path:
    return _queue_root(root) / "runs"


def _task_path(root: Path, task_id: str) -> Path:
    return _tasks(root) / f"{task_id}.json"


def _receipt_path(root: Path, task_id: str) -> Path:
    return _receipts(root) / f"{task_id}.json"


def _journal(root: Path, event: dict) -> None:
    path = _queue_root(root) / "journal.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _tail(text: object) -> str:
    if not isinstance(text, str):
        return ""
    return text[-MAX_TAIL_CHARS:]


def _relative_output(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected_outputs entries must be non-empty relative paths")
    path = Path(value)
    if path.is_absolute() or path.anchor or any(part == ".." for part in path.parts):
        raise ValueError(f"expected output escapes the workspace: {value!r}")
    if str(path) in {"", "."}:
        raise ValueError("expected output must name a file")
    return path.as_posix()


def _normalize(manifest: dict) -> dict:
    if not isinstance(manifest, dict):
        raise ValueError("task manifest must be an object")
    task_id = manifest.get("task_id")
    if not isinstance(task_id, str) or TASK_ID.fullmatch(task_id) is None:
        raise ValueError("task_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    workspace = manifest.get("workspace")
    if not isinstance(workspace, str) or not workspace.strip():
        raise ValueError("workspace is required")
    workspace_path = Path(workspace).expanduser()
    if not workspace_path.is_absolute():
        workspace_path = (Path.cwd() / workspace_path).resolve()
    if not workspace_path.is_dir():
        raise ValueError(f"workspace is not an existing directory: {workspace_path}")
    argv = manifest.get("argv")
    if (not isinstance(argv, list) or not argv
            or any(not isinstance(value, str) or "\0" in value for value in argv)
            or not argv[0]):
        raise ValueError("argv must be a nonempty string array")
    timeout = manifest.get("timeout_seconds", 900)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout_seconds must be a number")
    timeout = float(timeout)
    if not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be in (0, {MAX_TIMEOUT_SECONDS}]")
    outputs = manifest.get("expected_outputs", [])
    if not isinstance(outputs, list):
        raise ValueError("expected_outputs must be a list")
    outputs = [_relative_output(item) for item in outputs]
    depends_on = manifest.get("depends_on", [])
    if not isinstance(depends_on, list):
        raise ValueError("depends_on must be a list")
    dependencies = []
    for item in depends_on:
        if not isinstance(item, str) or TASK_ID.fullmatch(item) is None:
            raise ValueError("depends_on entries must be task ids")
        if item == task_id:
            raise ValueError("a task cannot depend on itself")
        if item not in dependencies:
            dependencies.append(item)
    priority = manifest.get("priority", 0)
    if isinstance(priority, bool) or not isinstance(priority, int) or not -1000 <= priority <= 1000:
        raise ValueError("priority must be an integer in [-1000, 1000]")
    stdin = manifest.get("stdin", "")
    if not isinstance(stdin, str):
        raise ValueError("stdin must be a string")
    max_output_bytes = manifest.get("max_output_bytes", external_runner.DEFAULT_MAX_OUTPUT_BYTES)
    if (isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int)
            or not 1 <= max_output_bytes <= 8 * 1024 * 1024):
        raise ValueError("max_output_bytes must be an integer between 1 and 8388608")
    created_by = manifest.get("created_by", "kernel")
    goal = manifest.get("goal", "")
    if not isinstance(created_by, str) or not created_by.strip():
        raise ValueError("created_by must be a non-empty string")
    if not isinstance(goal, str):
        raise ValueError("goal must be a string")
    return {
        "schema": TASK_SCHEMA,
        "task_id": task_id,
        "workspace": str(workspace_path),
        "goal": goal,
        "argv": list(argv),
        "stdin": stdin,
        "timeout_seconds": timeout,
        "max_output_bytes": max_output_bytes,
        "expected_outputs": outputs,
        "depends_on": dependencies,
        "priority": priority,
        "created_by": created_by,
    }


def _read_task(root: Path, task_id: str) -> dict:
    path = _task_path(root, task_id)
    if not path.is_file():
        raise ValueError(f"unknown task: {task_id}")
    record = _read_json(path)
    if record.get("schema") != RECORD_SCHEMA:
        raise ValueError(f"task record has an unexpected schema: {task_id}")
    manifest = record.get("manifest")
    if not isinstance(manifest, dict):
        raise ValueError(f"task record has no manifest: {task_id}")
    if record.get("manifest_sha256") != _digest(manifest):
        raise ValueError(f"task manifest digest does not match: {task_id}")
    return manifest


def _read_receipt(root: Path, task_id: str) -> dict | None:
    path = _receipt_path(root, task_id)
    if not path.is_file():
        return None
    receipt = _read_json(path)
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise ValueError(f"receipt has an unexpected schema: {task_id}")
    return receipt


def _assert_acyclic(root: Path, task_id: str, depends_on: list[str]) -> None:
    graph: dict[str, set[str]] = {}
    for path in sorted(_tasks(root).glob("*.json")):
        try:
            record = _read_json(path)
            manifest = record.get("manifest") or {}
            name = manifest.get("task_id")
            deps = manifest.get("depends_on") or []
            if isinstance(name, str) and isinstance(deps, list):
                graph[name] = {item for item in deps if isinstance(item, str)}
        except (OSError, ValueError):
            continue
    graph[task_id] = set(depends_on)
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(name: str) -> None:
        if name in done:
            return
        if name in visiting:
            raise ValueError(f"dependency cycle would be created at {name}")
        visiting.add(name)
        for child in sorted(graph.get(name, ())):
            visit(child)
        visiting.remove(name)
        done.add(name)

    visit(task_id)


def enqueue(root, manifest: dict) -> dict:
    """Store one immutable task.  Storing it does not execute anything."""
    root = Path(root)
    normalized = _normalize(manifest)
    task_id = normalized["task_id"]
    digest = _digest(normalized)
    path = _task_path(root, task_id)
    _assert_acyclic(root, task_id, normalized["depends_on"])
    if path.exists():
        existing = _read_task(root, task_id)
        if _digest(existing) == digest:
            return {"schema": STATUS_SCHEMA, "task_id": task_id, "state": "ALREADY_QUEUED",
                    "manifest_sha256": digest,
                    "note": "the same manifest is already queued; nothing changed"}
        raise ValueError(f"task id already queued with a different manifest: {task_id}")
    _atomic_json(path, {"schema": RECORD_SCHEMA, "task_id": task_id,
                        "manifest_sha256": digest, "manifest": normalized})
    now = _now()
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "task_id": task_id,
        "state": "QUEUED",
        "attempt": 0,
        "enqueued_at": now,
        "updated_at": now,
        "workspace": normalized["workspace"],
        "goal": normalized["goal"],
        "priority": normalized["priority"],
        "depends_on": normalized["depends_on"],
        "manifest_sha256": digest,
        "history": [],
        "note": "QUEUED only means stored; no process has run and no agent has ACKed.",
    }
    _atomic_json(_receipt_path(root, task_id), receipt)
    _journal(root, {"event": "enqueued", "at": now, "task_id": task_id,
                    "manifest_sha256": digest, "priority": normalized["priority"]})
    return {"schema": STATUS_SCHEMA, "task_id": task_id, "state": "QUEUED",
            "manifest_sha256": digest, "receipt": receipt}


def status(root) -> dict:
    root = Path(root)
    rows = []
    counts = {state: 0 for state in STATES}
    for path in sorted(_tasks(root).glob("*.json")):
        task_id = path.stem
        try:
            manifest = _read_task(root, task_id)
            receipt = _read_receipt(root, task_id) or {
                "state": "QUEUED", "attempt": 0, "note": "receipt missing; task recorded only"}
        except (OSError, ValueError) as exc:
            rows.append({"task_id": task_id, "state": "CORRUPT", "error": str(exc)})
            counts.setdefault("CORRUPT", 0)
            counts["CORRUPT"] += 1
            continue
        state = str(receipt.get("state", "UNKNOWN"))
        counts[state] = counts.get(state, 0) + 1
        rows.append({
            "task_id": task_id,
            "state": state,
            "attempt": receipt.get("attempt", 0),
            "priority": manifest["priority"],
            "goal": manifest["goal"],
            "depends_on": manifest["depends_on"],
            "enqueued_at": receipt.get("enqueued_at", ""),
            "updated_at": receipt.get("updated_at", ""),
            "error": receipt.get("error", ""),
            "replay_allowed": bool(receipt.get("replay_allowed", False)),
        })
    rows.sort(key=lambda row: (-int(row.get("priority") or 0),
                               str(row.get("enqueued_at") or ""),
                               str(row.get("task_id") or "")))
    return {"schema": STATUS_SCHEMA, "coord_root": str(root.resolve()),
            "tasks": rows, "counts": counts,
            "note": "SUCCEEDED is process/output evidence for this queue; it is not an agent ACK."}


def _readiness(root: Path, manifest: dict) -> tuple[str, str]:
    for dependency in manifest["depends_on"]:
        try:
            read = _read_receipt(root, dependency)
        except (OSError, ValueError) as exc:
            return "blocked", f"dependency_unreadable:{dependency}:{exc}"
        if read is None:
            return "pending", f"dependency_missing:{dependency}"
        state = str(read.get("state", "UNKNOWN"))
        if state == "SUCCEEDED":
            continue
        if state in TERMINAL:
            return "blocked", f"dependency_{state}:{dependency}"
        return "pending", f"dependency_{state}:{dependency}"
    return "ready", ""


def _blocked(root: Path, task_id: str, receipt: dict, reason: str) -> dict:
    now = _now()
    receipt.update({"state": "BLOCKED", "finished_at": now, "updated_at": now,
                    "error": reason, "replay_allowed": False,
                    "note": "a dependency is terminal without SUCCEEDED; no process was started."})
    _atomic_json(_receipt_path(root, task_id), receipt)
    _journal(root, {"event": "blocked", "at": now, "task_id": task_id, "reason": reason})
    return receipt


def _safe_outputs(workspace: Path, expected: list[str]) -> tuple[list[dict], list[str]]:
    outputs, missing = [], []
    root = workspace.resolve()
    for relative in expected:
        target = (root / relative).resolve()
        if target != root and root not in target.parents:
            missing.append(relative)
            continue
        if not target.is_file():
            missing.append(relative)
            continue
        digest = sha256()
        size = 0
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        outputs.append({"path": relative, "bytes": size, "sha256": digest.hexdigest()})
    return outputs, missing


def _finish(root: Path, task_id: str, receipt: dict, result: dict,
            manifest: dict) -> dict:
    now = _now()
    runner_state = str(result.get("state", "UNKNOWN"))
    worker = result.get("worker") if isinstance(result.get("worker"), dict) else {}
    exit_code = result.get("exit_code")
    if exit_code is None:
        exit_code = worker.get("exit_code")
    timed_out = result.get("timed_out")
    if timed_out is None:
        timed_out = worker.get("timed_out", False)
    stdout = result.get("stdout") if result.get("stdout") else worker.get("stdout", "")
    stderr = result.get("stderr") if result.get("stderr") else worker.get("stderr", "")
    error = ""
    outputs: list[dict] = []
    missing: list[str] = []
    if runner_state == "PROCESS_EXITED" and exit_code == 0:
        outputs, missing = _safe_outputs(Path(manifest["workspace"]),
                                         manifest["expected_outputs"])
        state = "SUCCEEDED" if not missing else "FAILED"
        if missing:
            error = "missing_expected_outputs:" + ",".join(missing)
    elif runner_state == "PROCESS_FAILED":
        state, error = "FAILED", f"process_exit_{exit_code}"
    elif runner_state == "NOT_STARTED":
        state, error = "FAILED", str(result.get("error") or "runner did not start")
    elif runner_state == "UNKNOWN":
        state = "UNKNOWN"
        error = str(result.get("error") or "runner outcome is unknown; do not replay")
    else:
        state = "UNKNOWN"
        error = f"unexpected_runner_state:{runner_state}"
    receipt.update({
        "state": state,
        "finished_at": now,
        "updated_at": now,
        "runner_state": runner_state,
        "exit_code": exit_code,
        "timed_out": bool(timed_out),
        "outputs": outputs,
        "missing_outputs": missing,
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
        "error": error,
        "replay_allowed": False,
        "note": ("Process and declared-output evidence only; this is not an agent ACK. "
                 "FAILED and UNKNOWN are terminal until an explicit requeue."),
    })
    _atomic_json(_receipt_path(root, task_id), receipt)
    _journal(root, {"event": "finished", "at": now, "task_id": task_id,
                    "state": state, "runner_state": runner_state, "exit_code": exit_code,
                    "outputs": [row["path"] for row in outputs], "error": error})
    return receipt


def _execute(root: Path, task_id: str, manifest: dict, receipt: dict) -> dict:
    workspace = Path(manifest["workspace"])
    if not workspace.is_dir():
        now = _now()
        receipt.update({"state": "FAILED", "finished_at": now, "updated_at": now,
                        "error": f"workspace_missing:{workspace}", "replay_allowed": False})
        _atomic_json(_receipt_path(root, task_id), receipt)
        _journal(root, {"event": "finished", "at": now, "task_id": task_id,
                        "state": "FAILED", "error": receipt["error"]})
        return receipt
    attempt = int(receipt.get("attempt", 0)) + 1
    event_id = f"UNATTENDED-{task_id}" + ("" if attempt == 1 else f"-R{attempt}")
    started = _now()
    receipt.update({"state": "RUNNING", "attempt": attempt, "event_id": event_id,
                    "started_at": started, "finished_at": "", "updated_at": started,
                    "error": "", "outputs": [], "missing_outputs": [],
                    "stdout_tail": "", "stderr_tail": "", "replay_allowed": False,
                    "note": "the command is running; a crash leaves this state unresolved."})
    _atomic_json(_receipt_path(root, task_id), receipt)
    _journal(root, {"event": "started", "at": started, "task_id": task_id,
                    "attempt": attempt, "event_id": event_id})
    try:
        result = external_runner.execute(
            _runs(root), event_id=event_id, workspace=workspace,
            plan={"argv": list(manifest["argv"])}, text=manifest["stdin"],
            timeout=manifest["timeout_seconds"],
            max_output_bytes=manifest["max_output_bytes"])
    except (OSError, ValueError, LockTimeout) as exc:
        now = _now()
        receipt.update({"state": "FAILED", "finished_at": now, "updated_at": now,
                        "error": f"runner_refused:{type(exc).__name__}:{exc}",
                        "replay_allowed": False})
        _atomic_json(_receipt_path(root, task_id), receipt)
        _journal(root, {"event": "finished", "at": now, "task_id": task_id,
                        "state": "FAILED", "error": receipt["error"]})
        return receipt
    return _finish(root, task_id, receipt, result, manifest)


def _queued(root: Path) -> list[tuple[dict, dict]]:
    rows = []
    for path in sorted(_tasks(root).glob("*.json")):
        task_id = path.stem
        try:
            manifest = _read_task(root, task_id)
            receipt = _read_receipt(root, task_id)
        except (OSError, ValueError):
            continue
        if receipt is None or receipt.get("state") in {"QUEUED", "BLOCKED"}:
            rows.append((manifest, receipt or {
                "schema": RECEIPT_SCHEMA, "task_id": task_id, "state": "QUEUED",
                "attempt": 0, "enqueued_at": "", "updated_at": "",
                "workspace": manifest["workspace"], "goal": manifest["goal"],
                "priority": manifest["priority"], "depends_on": manifest["depends_on"],
                "history": [], "note": "receipt missing; treated as QUEUED"}))
    rows.sort(key=lambda pair: (-int(pair[0]["priority"]),
                                str(pair[1].get("enqueued_at") or ""),
                                pair[0]["task_id"]))
    return rows


def _run_locked(root: Path, task_id: str | None) -> dict:
    if task_id is not None:
        manifest = _read_task(root, task_id)
        receipt = _read_receipt(root, task_id)
        if receipt is None:
            raise ValueError(f"task has no receipt: {task_id}")
        state = str(receipt.get("state", "UNKNOWN"))
        if state not in {"QUEUED", "BLOCKED"}:
            return {"schema": STATUS_SCHEMA, "task_id": task_id, "state": state,
                    "attempt": receipt.get("attempt", 0),
                    "note": "terminal or in-flight; use requeue only with new evidence"}
        readiness, reason = _readiness(root, manifest)
        if readiness == "blocked":
            return _blocked(root, task_id, receipt, reason)
        if readiness == "pending":
            return {"schema": STATUS_SCHEMA, "task_id": task_id, "state": "WAITING",
                    "reason": reason,
                    "note": "dependencies are not settled; nothing was started"}
        return _execute(root, task_id, manifest, receipt)

    pending = []
    blocked = []
    for manifest, receipt in _queued(root):
        readiness, reason = _readiness(root, manifest)
        if readiness == "ready":
            return _execute(root, manifest["task_id"], manifest, receipt)
        if readiness == "blocked":
            blocked.append((manifest, receipt, reason))
        else:
            pending.append((manifest, reason))
    if blocked:
        manifest, receipt, reason = blocked[0]
        return _blocked(root, manifest["task_id"], receipt, reason)
    if pending:
        return {"schema": STATUS_SCHEMA, "state": "IDLE",
                "reason": pending[0][1],
                "note": "no ready task; dependencies are still queued or missing"}
    return {"schema": STATUS_SCHEMA, "state": "IDLE",
            "note": "the queue is empty or every task is terminal"}


def run_once(root, *, task_id: str | None = None) -> dict:
    """Execute at most one ready task under an exclusive runner lock."""
    root = Path(root)
    queue = _queue_root(root)
    queue.mkdir(parents=True, exist_ok=True)
    try:
        with workspace_lock(queue / "runner.lock", timeout=0.5):
            return _run_locked(root, task_id)
    except LockTimeout:
        return {"schema": STATUS_SCHEMA, "state": "RUNNER_BUSY",
                "note": "another local runner holds the lock; no task was started"}


def requeue(root, task_id: str, *, actor: str, reason: str,
            allow_unknown: bool = False) -> dict:
    """Explicitly make a terminal task runnable again with a new event id."""
    root = Path(root)
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor is required")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason is required")
    queue = _queue_root(root)
    queue.mkdir(parents=True, exist_ok=True)
    try:
        with workspace_lock(queue / "runner.lock", timeout=0.5):
            manifest = _read_task(root, task_id)
            receipt = _read_receipt(root, task_id)
            if receipt is None:
                raise ValueError(f"task has no receipt: {task_id}")
            state = str(receipt.get("state", "UNKNOWN"))
            if state == "QUEUED":
                return {"schema": STATUS_SCHEMA, "task_id": task_id, "state": "QUEUED",
                        "note": "already queued; nothing changed"}
            if state == "SUCCEEDED":
                raise ValueError("a SUCCEEDED task cannot be requeued; enqueue a new task")
            if state not in {"FAILED", "BLOCKED", "UNKNOWN", "RUNNING", "CANCELLED"}:
                raise ValueError(f"state cannot be requeued: {state}")
            if state in {"UNKNOWN", "RUNNING"} and not allow_unknown:
                raise ValueError("UNKNOWN/RUNNING requires allow_unknown: the first command may have run")
            history = list(receipt.get("history") or [])
            history.append({key: receipt.get(key) for key in (
                "attempt", "event_id", "state", "started_at", "finished_at",
                "runner_state", "exit_code", "error", "outputs")})
            now = _now()
            receipt.update({"state": "QUEUED", "updated_at": now, "history": history,
                            "started_at": "", "finished_at": "", "error": "",
                            "outputs": [], "missing_outputs": [], "stdout_tail": "",
                            "stderr_tail": "", "replay_allowed": False,
                            "requeued_by": actor.strip(), "requeue_reason": reason.strip(),
                            "note": "explicitly requeued; the next run uses a new event id"})
            _atomic_json(_receipt_path(root, task_id), receipt)
            _journal(root, {"event": "requeued", "at": now, "task_id": task_id,
                            "actor": actor.strip(), "reason": reason.strip(),
                            "previous_state": state, "next_attempt": int(receipt["attempt"]) + 1})
            return {"schema": STATUS_SCHEMA, "task_id": task_id, "state": "QUEUED",
                    "receipt": receipt}
    except LockTimeout:
        return {"schema": STATUS_SCHEMA, "task_id": task_id, "state": "RUNNER_BUSY",
                "note": "another local runner holds the lock; nothing changed"}


def cancel(root, task_id: str, *, actor: str, reason: str) -> dict:
    root = Path(root)
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor is required")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason is required")
    queue = _queue_root(root)
    queue.mkdir(parents=True, exist_ok=True)
    try:
        with workspace_lock(queue / "runner.lock", timeout=0.5):
            _read_task(root, task_id)
            receipt = _read_receipt(root, task_id)
            if receipt is None:
                raise ValueError(f"task has no receipt: {task_id}")
            state = str(receipt.get("state", "UNKNOWN"))
            if state not in {"QUEUED", "BLOCKED"}:
                raise ValueError(f"only QUEUED or BLOCKED can be cancelled without killing a process; state is {state}")
            now = _now()
            receipt.update({"state": "CANCELLED", "updated_at": now, "finished_at": now,
                            "cancelled_by": actor.strip(), "cancel_reason": reason.strip(),
                            "note": "cancelled before a process was started"})
            _atomic_json(_receipt_path(root, task_id), receipt)
            _journal(root, {"event": "cancelled", "at": now, "task_id": task_id,
                            "actor": actor.strip(), "reason": reason.strip(),
                            "previous_state": state})
            return {"schema": STATUS_SCHEMA, "task_id": task_id, "state": "CANCELLED",
                    "receipt": receipt}
    except LockTimeout:
        return {"schema": STATUS_SCHEMA, "task_id": task_id, "state": "RUNNER_BUSY",
                "note": "another local runner holds the lock; nothing changed"}


def serve(root, *, interval: float = 30.0, max_ticks: int = 0,
          stop_file: Path | None = None, sleep=time.sleep,
          stream=None) -> dict:
    """Drain ready tasks until the queue is idle or ``max_ticks`` is reached."""
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) \
            or not math.isfinite(interval) or interval <= 0:
        raise ValueError("interval must be a finite positive number")
    if isinstance(max_ticks, bool) or not isinstance(max_ticks, int) or max_ticks < 0:
        raise ValueError("max_ticks must be a non-negative integer")
    stop = Path(stop_file) if stop_file is not None else None
    ticks, last = 0, {"state": "IDLE"}
    while True:
        if stop is not None and stop.exists():
            last = {"state": "STOPPED", "note": f"stop file exists: {stop}"}
            break
        result = run_once(root)
        ticks += 1
        last = result
        if stream is not None:
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            stream.flush()
        if max_ticks and ticks >= max_ticks:
            break
        state = str(result.get("state", ""))
        if state in {"IDLE", "STOPPED"}:
            break
        if state in {"RUNNER_BUSY", "WAITING"}:
            sleep(interval)
    return {"ticks": ticks, "last": last, "status": status(root)}


def _derived_task_id(task_id: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", task_id).strip("-._")[:100]
    return (base or "task") + "-local"


def dispatch_or_queue(coord_root, federation_root, *, workspace_id: str, task_id: str,
                      requirement: str, agents, local_manifest: dict | None = None,
                      sender: str = "kernel", priority: str = "normal",
                      ttl_hours: float = 24.0, allow_launch: bool = False,
                      urgent_interject: bool = False, transport: str = "auto",
                      moment=None, presence_snapshot: dict | None = None) -> dict:
    """Route one task to a fresh online agent, or keep it locally if none exist.

    Online presence is a self-report with a short lease.  Choosing that agent
    starts the normal assignment path, but delivery and acceptance still need
    their own evidence.  The local branch is intentionally an explicit plan:
    this function never guesses a command to run in someone else's name.
    """
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise ValueError("workspace_id is required")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id is required")
    if not isinstance(requirement, str) or not requirement.strip():
        raise ValueError("requirement is required")
    agents = [str(agent).strip() for agent in agents if str(agent).strip()]
    if not agents:
        raise ValueError("at least one agent is required")
    snap = presence_snapshot if presence_snapshot is not None else presence.snapshot(Path(coord_root))
    online = {row.get("agent") for row in snap.get("agents", []) if row.get("state") == "online"}
    candidates = [agent for agent in agents if agent in online]
    if candidates:
        receipt = assignment.assign(
            Path(coord_root), workspace_id=workspace_id, task_id=task_id,
            requirement=requirement, target=candidates[0], sender=sender,
            priority=priority, ttl_hours=ttl_hours, allow_launch=allow_launch,
            transport=transport, urgent_interject=urgent_interject, moment=moment,
            federation_root=Path(federation_root) if federation_root else None)
        return {"schema": "aep-unattended-dispatch-v1", "route": "agent",
                "target": candidates[0], "presence": snap.get("strength", {}),
                "assignment": receipt,
                "note": "online presence is not acceptance; delivery and ACK still need evidence"}
    if local_manifest is not None:
        if not isinstance(local_manifest, dict):
            raise ValueError("local_manifest must be an object")
        plan = dict(local_manifest)
        plan.setdefault("task_id", _derived_task_id(task_id))
        plan.setdefault("goal", requirement.strip())
        plan.setdefault("created_by", sender)
        queued = enqueue(coord_root, plan)
        return {"schema": "aep-unattended-dispatch-v1", "route": "local",
                "presence": snap.get("strength", {}), "queue": queued,
                "note": "no listed agent is online; the local command is queued, not yet run"}
    return {"schema": "aep-unattended-dispatch-v1", "route": "waiting",
            "presence": snap.get("strength", {}), "agents": agents,
            "reason": "no_online_agent_and_no_local_plan",
            "next": "supply a local plan, or queue a wake and let the supervisor reassign"}
