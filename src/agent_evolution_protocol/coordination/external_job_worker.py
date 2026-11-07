"""Trusted Windows Job bootstrap for one bounded external CLI execution.

This file is launched by absolute path with ``python -I -S``.  It joins the
parent-owned ``AEPExternal`` job *before* starting the requested CLI, so a
timeout in the parent can terminate the complete worker/target descendant
tree.  It is a containment and observability boundary, not an authorization
boundary: the parent is responsible for choosing a plan and for session lease
ownership.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from uuid import uuid4


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _read_signed(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid external execution record")
    unsigned = {key: item for key, item in value.items() if key != "sha256"}
    if value.get("sha256") != _digest(unsigned):
        raise ValueError("external execution record integrity failure")
    return value


def _atomic_signed(path: Path, value: dict) -> None:
    unsigned = dict(value)
    unsigned.pop("sha256", None)
    signed = {**unsigned, "sha256": _digest(unsigned)}
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(signed, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _job_module():
    # -I omits even this script's directory.  Add only the trusted module
    # directory; never let the target workspace alter the bootstrap imports.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import windows_job
    return windows_job


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
        # The parent may terminate the Job while the pipe is being drained.
        # The receipt records capture completeness instead of inventing output.
        return


def _close_stream(stream) -> None:
    if stream is not None:
        try:
            stream.close()
        except OSError:
            pass


def _run_target(argv: list[str], cwd: Path, text: str, limit: int) -> dict:
    stdout, stderr = _BoundedCapture(limit), _BoundedCapture(limit)
    process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               shell=False)
    readers = [
        threading.Thread(target=_drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=_drain, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        try:
            if text:
                process.stdin.write(text.encode("utf-8"))
                process.stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            _close_stream(process.stdin)
        code = process.wait()
    finally:
        # A Job termination can close the target without closing inherited
        # pipe handles immediately.  Never let a reader hold the bootstrap.
        for reader in readers:
            reader.join(1.0)
        incomplete = any(reader.is_alive() for reader in readers)
        if incomplete:
            _close_stream(process.stdout)
            _close_stream(process.stderr)
            for reader in readers:
                reader.join(0.2)
        incomplete = incomplete or any(reader.is_alive() for reader in readers)
        _close_stream(process.stdout)
        _close_stream(process.stderr)
    return {
        "state": "PROCESS_EXITED" if code == 0 else "PROCESS_FAILED",
        "exit_code": code,
        "stdout": stdout.text(),
        "stderr": stderr.text(),
        "stdout_bytes_seen": stdout.seen,
        "stderr_bytes_seen": stderr.seen,
        "stdout_truncated": stdout.truncated,
        "stderr_truncated": stderr.truncated,
        "capture_complete": not incomplete,
    }


def _validate_request(request: dict, execution: dict, job_name: str, request_path: Path) -> tuple[dict, Path, int]:
    if not sys.flags.isolated or not sys.flags.no_site:
        raise ValueError("external bootstrap requires Python -I -S")
    if not request_path.is_absolute() or request_path.name != "external-request.json":
        raise ValueError("absolute external-request.json path required")
    if not re.fullmatch(r"AEPExternal-[0-9a-f]{32}", job_name):
        raise ValueError("invalid external job name")
    if request.get("schema_version") != 1 or execution.get("schema_version") != 1:
        raise ValueError("unsupported external execution schema")
    event_id = request.get("event_id")
    payload = request.get("request")
    if (not isinstance(event_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", event_id) is None
            or not isinstance(payload, dict) or request.get("request_sha256") != _digest(payload)
            or execution.get("event_id") != event_id
            or execution.get("request_sha256") != request["request_sha256"]
            or execution.get("job_name") != job_name):
        raise ValueError("external execution request binding mismatch")
    workspace = payload.get("workspace")
    argv, text = payload.get("argv"), payload.get("stdin")
    limit = request.get("max_output_bytes")
    if (not isinstance(workspace, str) or not Path(workspace).is_absolute() or not Path(workspace).is_dir()
            or not isinstance(argv, list) or not argv
            or any(not isinstance(value, str) or "\0" in value for value in argv)
            or not isinstance(text, str)
            or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 8 * 1024 * 1024):
        raise ValueError("invalid external execution payload")
    if execution.get("context") != _job_module().execution_context():
        raise ValueError("external job belongs to another machine or Windows session")
    return payload, Path(workspace), limit


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    try:
        request_path = Path(args.request)
        request = _read_signed(request_path)
        execution = _read_signed(request_path.with_name("external-execution.json"))
        payload, workspace, limit = _validate_request(request, execution, args.job, request_path)
        job = _job_module().Job.open(args.job)
        if job is None:
            raise ValueError("external parent job no longer exists; target was not started")
        try:
            # This is deliberately before Popen: every normally-created target
            # descendant is now owned by the parent-held Job Object.
            job.assign_current_process()
        finally:
            job.close()
        result = _run_target(payload["argv"], workspace, payload["stdin"], limit)
        _atomic_signed(request_path.with_name("external-output.json"), {
            "schema_version": 1,
            "event_id": request["event_id"],
            "request_sha256": request["request_sha256"],
            **result,
        })
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print("External bootstrap refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
