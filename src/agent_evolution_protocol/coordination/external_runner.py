"""Durable, bounded local CLI execution with session-aware attempt fences.

An external CLI exit is only process evidence. It never becomes an agent ACK
or a task result by itself. Input-capable plans additionally require a bound
provider/agent/session triple, use an OS-backed per-session lock, and retain an
``UNCERTAIN`` sidecar after a timeout or crash so another event cannot blindly
type into the same original conversation.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from uuid import uuid4

from agent_evolution_protocol.federation.locking import LockTimeout, workspace_lock

from .external_tools import local_worker_plan, message_plan, resolve


DEFAULT_MAX_OUTPUT_BYTES = 512 * 1024
BOOTSTRAP_MAX_OUTPUT_BYTES = 32 * 1024
SESSION_LOCK_SECONDS = 5.0
EVENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}")


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
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate(event_id: str, workspace: Path, plan: dict, text: str,
              timeout: float, max_output_bytes: int) -> list[str]:
    if not isinstance(event_id, str) or EVENT_ID.fullmatch(event_id) is None:
        raise ValueError("invalid event id")
    if not isinstance(workspace, Path) or not workspace.is_dir():
        raise ValueError("existing workspace required")
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number")
    if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or not 1 <= max_output_bytes <= 8 * 1024 * 1024:
        raise ValueError("max_output_bytes must be an integer between 1 and 8388608")
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object")
    argv = plan.get("argv")
    if (not isinstance(argv, list) or not argv
            or any(not isinstance(value, str) or "\0" in value for value in argv)
            or not argv[0]):
        raise ValueError("argv must be a nonempty string array")
    return argv


def _session_binding(plan: dict, workspace: Path) -> dict | None:
    """Return the mandatory identity for an input-capable plan.

    A provider-specific thread id alone is not enough in an execution receipt:
    it must also name the intended agent and concrete workspace. Hashing these
    values gives a filesystem-safe lock/state name without leaking a native
    session id into the path.
    """
    value = plan.get("input_capable", False)
    if not isinstance(value, bool):
        raise ValueError("input_capable must be a bool")
    if not value:
        return None
    fields = {name: plan.get(name) for name in ("provider", "target_agent", "session_id")}
    if any(not isinstance(item, str) or not item.strip() or "\0" in item for item in fields.values()):
        raise ValueError("input-capable plan needs provider, target_agent and existing session_id")
    return {"workspace": str(workspace.resolve()), **fields}


def _session_digest(binding: dict) -> str:
    return _digest(binding)


def _state_path(root: Path, binding: dict) -> Path:
    return root / "session_state" / (_session_digest(binding) + ".json")


def _read_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "UNCERTAIN", "error": "session state unreadable"}
    if not isinstance(row, dict) or not isinstance(row.get("state"), str):
        return {"state": "UNCERTAIN", "error": "session state malformed"}
    return row


def _write_state(path: Path, *, binding: dict, event_id: str, request_sha256: str,
                 state: str, **details: object) -> dict:
    row = {"schema": "aep-external-session-state-v1", "binding": binding,
           "event_id": event_id, "request_sha256": request_sha256,
           "state": state, "updated_at": time.time(), **details}
    _atomic_json(path, row)
    return row


def _active_session_state(row: dict | None) -> bool:
    return bool(row and row.get("state") in {"STARTING", "RUNNING", "UNCERTAIN"})


class _BoundedCapture:
    def __init__(self, limit: int):
        self.limit = limit
        self.data = bytearray()
        self.seen = 0
        self.truncated = False

    def append(self, chunk: bytes) -> None:
        self.seen += len(chunk)
        remaining = self.limit - len(self.data)
        if remaining > 0:
            self.data.extend(chunk[:remaining])
        if len(chunk) > remaining:
            self.truncated = True

    def text(self) -> str:
        return bytes(self.data).decode("utf-8", errors="replace")


def _drain(stream, capture: _BoundedCapture) -> None:
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            capture.append(chunk)
    except (OSError, ValueError):
        return


def _close(stream) -> None:
    if stream is not None:
        try:
            stream.close()
        except OSError:
            pass


def _run_process(argv: list[str], *, cwd: Path, text: str, timeout: float,
                 max_output_bytes: int, on_started=None, on_timeout=None) -> dict:
    """Run one process while continuously draining bounded stdout/stderr.

    ``communicate`` is deliberately avoided: a descendant that inherited a
    pipe can otherwise keep a timeout blocked long after the direct child was
    killed. On POSIX the runner owns a process group; Windows target processes
    are instead started through the Job bootstrap below.
    """
    stdout, stderr = _BoundedCapture(max_output_bytes), _BoundedCapture(max_output_bytes)
    process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, shell=False,
                               start_new_session=(os.name != "nt"))
    if on_started:
        on_started(process.pid)
    readers = [
        threading.Thread(target=_drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=_drain, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    incomplete = False
    code: int | None = None
    try:
        try:
            if text:
                process.stdin.write(text.encode("utf-8"))
                process.stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            _close(process.stdin)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if on_timeout:
                on_timeout()
            if process.poll() is None:
                if os.name == "nt":
                    process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            try:
                code = process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                code = None
    except BaseException:
        if on_timeout:
            on_timeout()
        if process.poll() is None:
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        raise
    finally:
        for reader in readers:
            reader.join(1.0)
        incomplete = any(reader.is_alive() for reader in readers)
        if incomplete:
            _close(process.stdout)
            _close(process.stderr)
            for reader in readers:
                reader.join(0.2)
        _close(process.stdout)
        _close(process.stderr)
    complete = not incomplete and not any(reader.is_alive() for reader in readers)
    return {
        "timed_out": timed_out,
        "exit_code": code,
        "stdout": stdout.text(), "stderr": stderr.text(),
        "stdout_bytes_seen": stdout.seen, "stderr_bytes_seen": stderr.seen,
        "stdout_truncated": stdout.truncated or not complete,
        "stderr_truncated": stderr.truncated or not complete,
        "capture_complete": complete,
    }


def _read_signed_output(path: Path, *, event_id: str, request_sha256: str) -> dict | None:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(row, dict):
        return None
    unsigned = {key: value for key, value in row.items() if key != "sha256"}
    if (row.get("sha256") != _digest(unsigned) or row.get("event_id") != event_id
            or row.get("request_sha256") != request_sha256):
        return None
    return row


def _run_windows_contained(root: Path, *, event_id: str, workspace: Path, request: dict,
                           request_sha256: str, timeout: float, max_output_bytes: int,
                           on_started=None) -> dict:
    """Run through a parent-owned Windows Job and a trusted bootstrap."""
    from .windows_job import Job, execution_context, interpreter_profile

    job_name = "AEPExternal-" + uuid4().hex
    job = Job.create(job_name)
    folder = root / "jobs" / event_id
    request_path = folder / "external-request.json"
    output_path = folder / "external-output.json"
    execution = {"schema_version": 1, "backend": "windows-job",
                 "event_id": event_id, "request_sha256": request_sha256,
                 "job_name": job_name, "context": execution_context()}
    request_record = {"schema_version": 1, "event_id": event_id,
                      "request_sha256": request_sha256, "request": request,
                      "max_output_bytes": max_output_bytes}
    _atomic_json(request_path, {**request_record, "sha256": _digest(request_record)})
    _atomic_json(request_path.with_name("external-execution.json"),
                 {**execution, "sha256": _digest(execution)})
    command = [sys.executable, "-I", "-S", str(Path(__file__).with_name("external_job_worker.py").resolve()),
               "--job", job_name, "--request", str(request_path.resolve())]
    worker: dict | None = None
    stopped = False
    result: dict | None = None
    try:
        worker = _run_process(command, cwd=workspace, text="", timeout=timeout,
                              max_output_bytes=BOOTSTRAP_MAX_OUTPUT_BYTES,
                              on_started=on_started, on_timeout=job.terminate)
        output = _read_signed_output(output_path, event_id=event_id, request_sha256=request_sha256)
        if worker["timed_out"]:
            result = {"state": "UNKNOWN", "error": "timeout; inspect native session before any new attempt",
                      "worker": worker, "job_name": job_name,
                      "interpreter_profile": interpreter_profile(command)}
        elif output is None:
            result = {"state": "UNKNOWN", "error": "contained worker did not publish a valid output receipt",
                      "worker": worker, "job_name": job_name,
                      "interpreter_profile": interpreter_profile(command)}
        else:
            result = {**{key: value for key, value in output.items() if key != "sha256"},
                      "job_name": job_name, "worker": worker,
                      "interpreter_profile": interpreter_profile(command)}
    finally:
        try:
            job.terminate()
            deadline = time.monotonic() + 3
            while job.active_processes() and time.monotonic() < deadline:
                time.sleep(0.02)
        except OSError:
            pass
        finally:
            try:
                job.close()
            except OSError:
                pass
        try:
            remaining = Job.open(job_name)
            if remaining is None:
                stopped = True
            else:
                remaining.close()
        except OSError:
            stopped = False
        if worker is not None:
            worker["process_tree_stopped_verified"] = stopped
    if result is None:
        raise RuntimeError("contained execution did not produce a result")
    result["process_tree_stopped_verified"] = stopped
    return result


def _run_direct(argv: list[str], *, workspace: Path, text: str, timeout: float,
                max_output_bytes: int, on_started=None) -> dict:
    worker = _run_process(argv, cwd=workspace, text=text, timeout=timeout,
                          max_output_bytes=max_output_bytes, on_started=on_started)
    if worker["timed_out"]:
        return {"state": "UNKNOWN", "error": "timeout; inspect native session before any new attempt",
                **worker, "process_tree_stopped_verified": False}
    return {"state": "PROCESS_EXITED" if worker["exit_code"] == 0 else "PROCESS_FAILED",
            **worker, "process_tree_stopped_verified": False}


def _existing(root: Path, event_id: str, digest: str) -> dict | None:
    attempt = root / (event_id + ".attempt.json")
    result_path = root / (event_id + ".result.json")
    if not attempt.exists():
        return None
    try:
        existing = json.loads(attempt.read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or "request_sha256" not in existing:
            raise ValueError("incomplete attempt")
    except (OSError, ValueError):
        return {"event_id": event_id, "state": "UNKNOWN", "replay_allowed": False,
                "error": "attempt incomplete or unreadable; reconcile before retry"}
    if existing["request_sha256"] != digest:
        raise ValueError("event id already reserved for a different request")
    if result_path.exists():
        try:
            saved = json.loads(result_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict) and saved.get("request_sha256") == digest and saved.get("event_id") == event_id:
                return saved
        except (OSError, ValueError):
            pass
    return {"event_id": event_id, "state": "UNKNOWN", "replay_allowed": False}


def _reserve(root: Path, event_id: str, request: dict, digest: str) -> None:
    attempt = root / (event_id + ".attempt.json")
    with attempt.open("x", encoding="utf-8") as handle:
        json.dump({"request_sha256": digest, "event_id": event_id,
                   "workspace": request["workspace"], "state": "STARTING"}, handle)
        handle.flush()
        os.fsync(handle.fileno())


def execute(root: Path, *, event_id: str, workspace: Path, plan: dict,
            text: str = "", timeout: float = 120,
            max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES) -> dict:
    """Execute exactly one plan, returning only evidence the runner observed.

    Session busy/uncertain responses intentionally do not reserve a new event:
    no local input was injected, and a coordinator must wait or reconcile the
    actual original conversation rather than manufacture retries.
    """
    root, workspace = Path(root), Path(workspace)
    argv = _validate(event_id, workspace, plan, text, timeout, max_output_bytes)
    request = {"argv": argv, "stdin": text, "workspace": str(workspace.resolve())}
    digest = _digest(request)
    binding = _session_binding(plan, workspace)
    root.mkdir(parents=True, exist_ok=True)
    lock = nullcontext()
    if binding:
        lock = workspace_lock(root / "session_locks" / (_session_digest(binding) + ".lock"),
                              timeout=min(SESSION_LOCK_SECONDS, float(timeout)))
    try:
        with lock:
            existing = _existing(root, event_id, digest)
            if existing is not None:
                return existing
            if binding:
                state_path = _state_path(root, binding)
                previous = _read_state(state_path)
                if _active_session_state(previous):
                    return {"event_id": event_id, "state": "SESSION_UNCERTAIN",
                            "replay_allowed": False, "execution_started": False,
                            "previous_event_id": previous.get("event_id", ""),
                            "next": "reconcile the original session before any new input"}
            try:
                _reserve(root, event_id, request, digest)
            except FileExistsError:
                # A second process won between the check and reservation.
                return _existing(root, event_id, digest) or {"event_id": event_id,
                                                              "state": "UNKNOWN",
                                                              "replay_allowed": False}
            state_path = _state_path(root, binding) if binding else None
            if state_path:
                _write_state(state_path, binding=binding, event_id=event_id,
                             request_sha256=digest, state="STARTING")

            def on_started(pid: int) -> None:
                if state_path:
                    _write_state(state_path, binding=binding, event_id=event_id,
                                 request_sha256=digest, state="RUNNING", pid=pid)

            result = {"event_id": event_id, "workspace": request["workspace"],
                      "request_sha256": digest, "delivery_verified": False,
                      "replay_allowed": False, "execution_started": True}
            try:
                if os.name == "nt":
                    execution = _run_windows_contained(root, event_id=event_id, workspace=workspace,
                                                       request=request, request_sha256=digest,
                                                       timeout=float(timeout), max_output_bytes=max_output_bytes,
                                                       on_started=on_started)
                else:
                    execution = _run_direct(argv, workspace=workspace, text=text,
                                            timeout=float(timeout), max_output_bytes=max_output_bytes,
                                            on_started=on_started)
                result.update(execution)
            except OSError as exc:
                result.update(state="NOT_STARTED", error=str(exc), execution_started=False)
            except BaseException:
                if state_path:
                    _write_state(state_path, binding=binding, event_id=event_id,
                                 request_sha256=digest, state="UNCERTAIN",
                                 error="runner interrupted; inspect native session")
                raise
            if state_path:
                next_state = "UNCERTAIN" if result.get("state") == "UNKNOWN" else result["state"]
                _write_state(state_path, binding=binding, event_id=event_id,
                             request_sha256=digest, state=next_state,
                             process_tree_stopped_verified=result.get("process_tree_stopped_verified", False))
            _atomic_json(root / (event_id + ".result.json"), result)
            return result
    except LockTimeout:
        return {"event_id": event_id, "state": "SESSION_BUSY", "replay_allowed": False,
                "execution_started": False,
                "next": "wait for the active session operation to settle; do not create another event"}


def reconcile_session(root: Path, *, workspace: Path, provider: str, target_agent: str,
                      session_id: str, evidence: str) -> dict:
    """Explicitly clear an uncertain session fence after inspecting real evidence.

    This never retries or sends a message. It merely records the human or
    adapter evidence that makes a future *new* event eligible to acquire the
    session lock.
    """
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("reconciliation requires concrete evidence")
    binding = _session_binding({"input_capable": True, "provider": provider,
                                "target_agent": target_agent, "session_id": session_id},
                               Path(workspace))
    root = Path(root)
    path = _state_path(root, binding)
    lock = workspace_lock(root / "session_locks" / (_session_digest(binding) + ".lock"),
                          timeout=SESSION_LOCK_SECONDS)
    try:
        with lock:
            previous = _read_state(path)
            if not previous:
                return {"state": "NO_SESSION_STATE", "reconciled": False}
            _write_state(path, binding=binding, event_id=str(previous.get("event_id", "")),
                         request_sha256=str(previous.get("request_sha256", "")), state="RECONCILED",
                         evidence=evidence.strip())
            return {"state": "RECONCILED", "reconciled": True,
                    "previous_state": previous.get("state", "")}
    except LockTimeout:
        return {"state": "SESSION_BUSY", "reconciled": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["desktop-read", "t3-send", "worker-resume", "reconcile-session"])
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--event-id")
    parser.add_argument("--session-id")
    parser.add_argument("--agent-type")
    parser.add_argument("--target-agent")
    parser.add_argument("--provider")
    parser.add_argument("--text-file", type=Path)
    parser.add_argument("--evidence")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--max-output-bytes", type=int, default=DEFAULT_MAX_OUTPUT_BYTES)
    args = parser.parse_args()
    if args.action == "reconcile-session":
        required = (args.provider, args.target_agent, args.session_id, args.evidence)
        if not all(required):
            parser.error("reconcile-session needs --provider --target-agent --session-id --evidence")
        result = reconcile_session(args.root, workspace=args.workspace, provider=args.provider,
                                   target_agent=args.target_agent, session_id=args.session_id,
                                   evidence=args.evidence)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["state"] in {"RECONCILED", "NO_SESSION_STATE"} else 1
    if not args.event_id:
        parser.error("--event-id required")
    text = args.text_file.read_text(encoding="utf-8") if args.text_file else ""
    if args.action == "desktop-read":
        prefix = resolve("agent_desktop")
        if not prefix:
            parser.error("agent-desktop not installed")
        plan = {"argv": [*prefix, "list-apps"], "input_capable": False}
    elif args.action == "t3-send":
        if not args.target_agent:
            parser.error("t3-send needs --target-agent to bind the original session")
        plan = message_plan("t3code", args.session_id, text, target_agent=args.target_agent)
    else:
        plan = local_worker_plan(args.agent_type, args.workspace, args.session_id)
        if not text.strip():
            parser.error("--text-file must contain a task")
    result = execute(args.root, event_id=args.event_id, workspace=args.workspace,
                     plan=plan, text=text, timeout=args.timeout,
                     max_output_bytes=args.max_output_bytes) if args.apply else plan
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not args.apply or result["state"] == "PROCESS_EXITED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
