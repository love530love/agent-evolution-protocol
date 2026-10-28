"""Contain trusted Windows tests in an owned job; never infer global cancellation."""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

from . import improvement, improvement_runs


def descriptor(folder, request):
    row = improvement_runs._read(folder / "execution.json")
    context = row.get("context")
    if (row.get("schema_version") != 1 or row.get("backend") != "windows-job"
            or row.get("id") != request["id"] or row.get("run_id") != request["run_id"]
            or row.get("request_sha256") != request["sha256"]
            or not isinstance(row.get("job_name"), str)
            or not re.fullmatch(r"AEPValidation-[0-9a-f]{32}", row["job_name"])
            or not isinstance(context, dict) or set(context) != {"machine", "session_id"}
            or not isinstance(context["machine"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", context["machine"])
            or type(context["session_id"]) is not int or context["session_id"] < 0):
        raise ValueError("invalid validation execution binding")
    return row


def observe_absence(execution):
    """Only absence is final: an empty existing job may have an unassigned worker."""
    from .windows_job import Job, execution_context
    if execution["context"] != execution_context():
        raise ValueError("recovery requires original Windows machine and session")
    job = Job.open(execution["job_name"])
    if job is None:
        return {"execution_stop_verified": True, "observation": "JOB_ABSENT",
                "stop_scope": "owned_windows_job", "process_tree_stopped_verified": False}
    try:
        active = job.active_processes()
    finally:
        job.close()
    raise ValueError(f"validation job still exists ({active} active); do not retry or force recovery")


def stop_record(folder, request):
    execution = descriptor(folder, request)
    row = improvement_runs._read(folder / "execution-stop.json")
    if (row.get("schema_version") != 1 or row.get("request_sha256") != request["sha256"]
            or row.get("execution_sha256") != execution["sha256"]
            or type(row.get("execution_stop_verified")) is not bool
            or row.get("stop_scope") != "owned_windows_job"
            or row.get("process_tree_stopped_verified") is not False
            or (row["execution_stop_verified"] and row.get("observation") != "JOB_ABSENT")):
        raise ValueError("invalid validation stop evidence")
    return row


def run(folder, request, output):
    if os.name != "nt":
        return subprocess.run(request["command"], cwd=request["workspace"], stdout=output,
                              stderr=subprocess.STDOUT, timeout=request["timeout"], check=False)
    from .windows_job import Job, execution_context
    context = execution_context()
    name = "AEPValidation-" + uuid4().hex
    job = Job.create(name)
    execution = improvement_runs._signed({"schema_version": 1, "backend": "windows-job",
        "id": request["id"], "run_id": request["run_id"], "request_sha256": request["sha256"],
        "job_name": name, "context": context})
    published = False
    try:
        # Persist ownership before starting a worker that can only OPEN this job.
        improvement._publish_new(folder / "execution.json", execution)
        published = True
        command = [sys.executable, "-I", "-S", str(Path(__file__).with_name("validation_worker.py").resolve()),
                   "--job", name, "--request", str((folder / "request.json").resolve())]
        return subprocess.run(command, cwd=request["workspace"], stdout=output, stderr=subprocess.STDOUT,
                              timeout=request["timeout"], check=False)
    finally:
        stop = {"schema_version": 1, "request_sha256": request["sha256"],
                "execution_sha256": execution["sha256"], "execution_stop_verified": False,
                "stop_scope": "owned_windows_job", "process_tree_stopped_verified": False}
        try:
            job.terminate()
            deadline = time.monotonic() + 3
            while job.active_processes() and time.monotonic() < deadline:
                time.sleep(.02)
            # Closing prevents a late bootstrap from opening a reusable empty job.
            job.close()
            stop.update(observe_absence(execution))
        except (OSError, ValueError) as exc:
            stop["error"] = str(exc)
        finally:
            job.close()
        if published:
            stop["observed_at"] = datetime.now(timezone.utc).isoformat()
            improvement._publish_new(folder / "execution-stop.json", improvement_runs._signed(stop))
