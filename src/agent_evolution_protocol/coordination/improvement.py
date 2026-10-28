"""Immutable, evidence-bound improvement review journal; never deploys changes."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

TRANSITIONS = {"PROPOSED": {"VALIDATED", "REJECTED"}, "VALIDATED": {"REVIEWED", "REJECTED"},
               "REVIEWED": {"TRIAL", "REJECTED"}, "TRIAL": {"ADOPTED", "ROLLED_BACK"},
               "ADOPTED": {"ROLLED_BACK"}, "REJECTED": set(), "ROLLED_BACK": set()}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _folder(root, identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{24}", identity):
        raise ValueError("invalid improvement id")
    return Path(root) / "improvements" / identity


def _evidence(paths):
    if not isinstance(paths, list) or not paths:
        raise ValueError("nonempty evidence file list required")
    rows = []
    for item in paths:
        path = Path(item).resolve()
        rows.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return rows


def _publish_new(path, value):
    """Publish a complete JSON file without overwriting a concurrent winner.

    Requires same-volume hard links (NTFS on Windows). Unsupported filesystems
    fail explicitly rather than falling back to an overwrite-prone rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _save(folder, index, row):
    row = {**row, "at": datetime.now(timezone.utc).isoformat()}
    row["sha256"] = _hash(row)
    folder.mkdir(parents=True, exist_ok=True)
    _publish_new(folder / f"{index:06d}.json", row)
    return row


def status(root, identity):
    rows = []
    for path in sorted(_folder(root, identity).glob("*.json")):
        if path.name != f"{len(rows):06d}.json":
            raise ValueError("journal sequence mismatch")
        row = json.loads(path.read_text(encoding="utf-8"))
        digest = row.pop("sha256")
        if _hash(row) != digest or row["previous"] != (rows[-1]["sha256"] if rows else None):
            raise ValueError("journal integrity failure")
        rows.append({**row, "sha256": digest})
    if not rows:
        raise ValueError("improvement not found")
    return {"id": identity, "state": rows[-1]["state"], "events": rows,
            "deployment_performed": False, "identity_authenticated": False}


def propose(root, request):
    fields = {"author", "change", "acceptance", "rollback", "scope", "evidence"}
    if not isinstance(request, dict) or set(request) != fields:
        raise ValueError("proposal requires author/change/acceptance/rollback/scope/evidence")
    if any(not isinstance(request[k], str) or not request[k].strip() for k in fields - {"evidence"}):
        raise ValueError("proposal text fields must be nonempty")
    frozen = {**request, "evidence": _evidence(request["evidence"])}
    identity = _hash(frozen)[:24]
    folder = _folder(root, identity)
    if not (folder / "000000.json").exists():
        try:
            _save(folder, 0, {"state": "PROPOSED", "actor": request["author"], "proposal": frozen, "previous": None})
        except FileExistsError:
            pass  # Concurrent identical proposal published first; verify it below.
    return status(root, identity)


def advance(root, identity, *, state, actor, evidence, note, token=None):
    from . import improvement_policy, improvement_work
    enforced = improvement_policy.read(root)["mode"] == "enforced"
    if state in {"VALIDATED", "REVIEWED", "TRIAL", "ADOPTED"} and (enforced or token is not None):
        if not token:
            raise ValueError("enforced mode requires an active claim token for promotion")
        lease = improvement_work.check_claim(root, identity, actor=actor, token=token)
        if lease["target"] != state:
            raise ValueError("claim does not authorize this stage transition")
    current = status(root, identity)
    if state not in TRANSITIONS[current["state"]]:
        raise ValueError("illegal improvement transition")
    if not isinstance(actor, str) or not actor.strip() or not isinstance(note, str) or not note.strip():
        raise ValueError("actor and decision note required")
    events = current["events"]
    if state == "REVIEWED" and actor in {e["actor"] for e in events}:
        raise ValueError("reviewer must differ from author and validator")
    # Freeze evidence at every stage and reject evidence changed since earlier stages.
    for event in events if state != "ROLLED_BACK" else []:
        for record in event.get("evidence", event.get("proposal", {}).get("evidence", [])):
            if hashlib.sha256(Path(record["path"]).read_bytes()).hexdigest() != record["sha256"]:
                raise ValueError("frozen evidence changed")
    records = _evidence(evidence)
    # Evidence hashing may outlive the lease or overlap another stage decision.
    if status(root, identity)["events"][-1]["sha256"] != events[-1]["sha256"]:
        raise ValueError("proposal changed during evidence verification")
    if state in {"VALIDATED", "REVIEWED", "TRIAL", "ADOPTED"} and (enforced or token is not None):
        improvement_work.check_claim(root, identity, actor=actor, token=token)
    _save(_folder(root, identity), len(events), {"state": state, "actor": actor, "note": note,
          "evidence": records, "previous": events[-1]["sha256"]})
    return status(root, identity)


def ingest(root, event_file):
    from .battle_orders import _task_writer_lock
    with _task_writer_lock(Path(root), "improvement-ingest"):
        return _ingest(root, event_file)


def _ingest(root, event_file):
    """Explicit completion-event ingestion; no watcher or implicit commands."""
    event = json.loads(Path(event_file).read_text(encoding="utf-8"))
    if not isinstance(event, dict) or set(event) != {"event_id", "kind", "improvement"}:
        raise ValueError("event requires event_id/kind/improvement")
    if event["kind"] not in {"TASK_COMPLETED", "TASK_FAILED"}:
        raise ValueError("unsupported completion event")
    if not isinstance(event["event_id"], str) or not event["event_id"].strip():
        raise ValueError("event_id required")
    request = event["improvement"]
    if not isinstance(request, dict):
        raise ValueError("improvement request required")
    request = {**request, "evidence": list(request.get("evidence", [])) + [str(Path(event_file).resolve())]}
    # Stable event ID reservation prevents changed payloads being replayed as new work.
    folder = Path(root) / "improvement_events"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (_hash(event["event_id"]) + ".json")
    digest = _hash(event)
    if path.exists():
        prior = json.loads(path.read_text(encoding="utf-8"))
        if prior["digest"] != digest:
            raise ValueError("event id reused with changed payload")
        return status(root, prior["id"])
    result = propose(root, request)
    _publish_new(path, {"digest": digest, "id": result["id"]})
    return result


def validate_tests(root, identity, *, workspace, actor, timeout=120, token=None):
    from .battle_orders import _task_writer_lock
    _folder(root, identity)  # Validate before deriving the execution lock name.
    with _task_writer_lock(Path(root), "improvement-validation-" + identity):
        return _validate_tests(root, identity, workspace=workspace, actor=actor, timeout=timeout, token=token)


def _validate_tests(root, identity, *, workspace, actor, timeout, token):
    """Run only repository unittest discovery, never execute commands from events.

    Tests are arbitrary code: caller must authorize/trust the workspace. This is a
    subprocess with a deadline, NOT a security sandbox or resource isolation.
    """
    from . import improvement_policy, improvement_runs, validation_execution
    from .battle_orders import _task_writer_lock
    if improvement_policy.read(root)["mode"] == "enforced" and not token:
        raise ValueError("enforced mode requires claim token before executing tests")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
        raise ValueError("timeout must be 1..300 seconds")
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor required before executing tests")
    current = status(root, identity)
    if current["state"] != "PROPOSED":
        raise ValueError("validation requires PROPOSED")
    if token is not None:
        from . import improvement_work
        improvement_work.brief(root, identity, actor=actor, token=token)
    workspace = Path(workspace).resolve()
    if not (workspace / "tests").is_dir():
        raise ValueError("workspace needs tests directory")
    report = {"command": [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
              "workspace": str(workspace), "security_sandbox": False, "status": "FAILED"}
    # Claim replacement and run reservation share this short lock. The execution
    # lock remains held for the process lifetime; no long global handoff lock.
    with _task_writer_lock(Path(root), "improvement-handoff"):
        improvement_runs.require_handoff(root, identity)
        if status(root, identity)["events"][-1]["sha256"] != current["events"][-1]["sha256"]:
            raise ValueError("proposal changed before validation reservation")
        if token is not None:
            improvement_work.check_claim(root, identity, actor=actor, token=token)
        else:
            from . import improvement_work
            lease = improvement_work.read_lease(root, identity, optional=True)
            if lease.get("active") and lease["head"] == current["events"][-1]["sha256"] and lease["expires"] > improvement_work._now():
                raise ValueError("active claim requires its token before executing tests")
        folder = _folder(root, identity) / "runs" / uuid4().hex
        request = improvement_runs.begin(root, identity, folder, head=current["events"][-1]["sha256"],
            actor=actor, token=token, command=report["command"], workspace=workspace, timeout=timeout)
    report.update(execution_stop_required=os.name == "nt", execution_stop_verified=False,
                  execution_backend="windows-job" if os.name == "nt" else "direct-subprocess")
    try:
        with (folder / "output.txt").open("w", encoding="utf-8") as output:
            run = validation_execution.run(folder, request, output)
        report["exit_code"] = run.returncode
        # unittest returns success for zero discovered tests: do not accept that.
        text = (folder / "output.txt").read_text(encoding="utf-8", errors="replace")
        count = re.search(r"Ran (\d+) tests? in", text)
        report["tests_run"] = int(count[1]) if count else 0
        if run.returncode == 0 and report["tests_run"] > 0:
            report["status"] = "PASSED"
    except (OSError, subprocess.TimeoutExpired) as exc:
        report["error"] = str(exc)
        if isinstance(exc, subprocess.TimeoutExpired):
            report["execution_stop_required"] = True
    if (folder / "execution-stop.json").exists():
        report["execution_stop_verified"] = validation_execution.stop_record(folder, request)["execution_stop_verified"]
    if report["execution_stop_required"] and not report["execution_stop_verified"]:
        report["status"] = "FAILED"
        report["error"] = report.get("error", "") + " Execution stop not verified; inspect before recovery."
    path = folder / "report.json"
    _publish_new(path, report)
    if report["status"] == "PASSED":
        proofs = [str(path), str(folder / "output.txt")]
        try:
            if token is not None:
                improvement_work.finish(root, identity, actor=actor, token=token,
                                        evidence=proofs, note="Executed unittest discovery passed")
            else:
                advance(root, identity, state="VALIDATED", actor=actor,
                    evidence=proofs, note="Executed unittest discovery passed")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report.update(status="PROMOTION_BLOCKED", promotion_error=str(exc))
            # Never rewrite a test report that a partially committed journal may reference.
            _publish_new(folder / "promotion.json", {
                "status": "PROMOTION_BLOCKED", "error": str(exc),
                "note": "Inspect current proposal before retrying; test success is not promotion."})
    receipt = improvement_runs.complete(folder, request, status=report["status"])
    return {**report, "report": str(path), "run_id": folder.name, "execution_receipt": receipt,
            "proposal": status(root, identity)}
