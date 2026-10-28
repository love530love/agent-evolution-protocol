"""Trusted bootstrap, invoked by absolute path with Python -I -S.

No workspace Python code is imported before joining the parent's existing job.
The actual tests retain the original interpreter/site behavior inside the job.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


def _read_signed(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid validation execution record")
    unsigned = {key: item for key, item in value.items() if key != "sha256"}
    digest = hashlib.sha256(json.dumps(unsigned, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if value.get("sha256") != digest:
        raise ValueError("validation execution record integrity failure")
    return value


def _job_module():
    # -I omits even the script directory. Add only this trusted module's own
    # directory, never the workspace or environment-provided import paths.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import windows_job
    return windows_job


def run_worker(job_name, command, cwd):
    """Join before launching the fixed unittest command; never create a job."""
    expected = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    if command != expected:
        raise ValueError("validation command must be the fixed unittest discovery command")
    job = _job_module().Job.open(job_name)
    if job is None:
        raise ValueError("validation parent job no longer exists; tests were not started")
    try:
        job.assign_current_process()
    finally:
        # The parent's handle is the sole long-lived job handle. If it has
        # disappeared, this close also terminates this assigned worker.
        job.close()
    return subprocess.run(command, cwd=cwd, check=False).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description="Contained validation bootstrap")
    parser.add_argument("--job", required=True)
    parser.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    try:
        if not sys.flags.isolated or not sys.flags.no_site:
            raise ValueError("validation bootstrap requires Python -I -S")
        request_path = Path(args.request)
        if not request_path.is_absolute() or request_path.name != "request.json":
            raise ValueError("absolute validation request.json path required")
        request = _read_signed(request_path)
        execution = _read_signed(request_path.with_name("execution.json"))
        identity, run_id = request.get("id"), request.get("run_id")
        if (request.get("schema_version") != 1 or execution.get("schema_version") != 1
                or not isinstance(identity, str) or re.fullmatch(r"[0-9a-f]{24}", identity) is None
                or not isinstance(run_id, str) or re.fullmatch(r"[0-9a-f]{32}", run_id) is None
                or request_path.parent.name != run_id
                or execution.get("id") != identity or execution.get("run_id") != run_id
                or execution.get("backend") != "windows-job"
                or execution.get("request_sha256") != request["sha256"]
                or execution.get("job_name") != args.job):
            raise ValueError("validation execution request binding mismatch")
        workspace = request.get("workspace")
        if not isinstance(workspace, str) or not Path(workspace).is_absolute():
            raise ValueError("absolute validation workspace required")
        if not (Path(workspace) / "tests").is_dir():
            raise ValueError("validation workspace needs tests directory")
        if execution.get("context") != _job_module().execution_context():
            raise ValueError("validation job belongs to another machine or Windows session")
        return run_worker(args.job, request.get("command"), workspace)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("Validation bootstrap refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
