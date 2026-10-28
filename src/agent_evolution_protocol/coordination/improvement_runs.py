"""Immutable validation receipts and explicit, scoped interrupted-run recovery."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from . import improvement


def _signed(value):
    return {**value, "sha256": improvement._hash(value)}


def _read(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid validation record")
    digest = value.get("sha256")
    unsigned = {key: item for key, item in value.items() if key != "sha256"}
    if not isinstance(digest, str) or digest != improvement._hash(unsigned):
        raise ValueError("validation record integrity failure")
    return value


def begin(root, identity, folder, *, head, actor, token, command, workspace, timeout):
    """Reserve before invoking a subprocess. Caller holds the proposal execution lock."""
    request = _signed({"schema_version": 1, "id": identity, "run_id": folder.name,
        "head": head, "actor": actor, "command": command, "workspace": str(workspace),
        "timeout": timeout, "started_at": datetime.now(timezone.utc).isoformat(),
        "claim_fingerprint": hashlib.sha256(token.encode()).hexdigest() if token is not None else None})
    improvement._publish_new(folder / "request.json", request)
    return request


def complete(folder, request, *, status):
    names = ["report.json", "output.txt"]
    if status == "PROMOTION_BLOCKED":
        names.append("promotion.json")
    names.extend(name for name in ("execution.json", "execution-stop.json") if (folder / name).exists())
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    receipt = _signed({"schema_version": 2, "id": request["id"], "run_id": request["run_id"],
        "request_sha256": request["sha256"], "status": status,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "files": {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in names},
        "execution_stop_required": report.get("execution_stop_required", False),
        "execution_stop_verified": report.get("execution_stop_verified", False),
        "process_tree_stopped_verified": False})
    improvement._publish_new(folder / "receipt.json", receipt)
    return str(folder / "receipt.json")


def _check_request(row, identity, run_id):
    if (row.get("schema_version") != 1 or row.get("id") != identity or row.get("run_id") != run_id
            or not isinstance(row.get("head"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["head"])
            or not isinstance(row.get("actor"), str) or not row["actor"].strip()
            or not isinstance(row.get("workspace"), str) or not row["workspace"]
            or not isinstance(row.get("command"), list) or not row["command"]
            or any(not isinstance(part, str) for part in row["command"])
            or type(row.get("timeout")) is not int or not 1 <= row["timeout"] <= 300):
        raise ValueError("invalid validation request")
    fingerprint = row.get("claim_fingerprint")
    if fingerprint is not None and (not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)):
        raise ValueError("invalid validation claim fingerprint")
    datetime.fromisoformat(row["started_at"])


def history(root, identity, *, verify_artifacts=True):
    """Read-only audit. Legacy reports are visible but never labelled verified."""
    current = improvement.status(root, identity)
    head = current["events"][-1]["sha256"]
    runs, legacy, blocked = [], [], []
    historical_count = 0
    for folder in sorted((improvement._folder(root, identity) / "runs").glob("*")):
        if not folder.is_dir():
            continue
        row = {"run_id": folder.name, "report": str(folder / "report.json"),
               "output": str(folder / "output.txt"), "receipt": str(folder / "receipt.json"),
               "process_tree_stopped_verified": False}
        belongs = True  # Unknown ownership must not hide a damaged record as historical.
        try:
            if not re.fullmatch(r"[0-9a-f]{32}", folder.name):
                raise ValueError("invalid validation run directory")
            if not (folder / "request.json").exists():
                if (folder / "receipt.json").exists():
                    raise ValueError("validation request missing")
                # Existing pre-upgrade reports lack a stage binding and trusted hashes.
                if not (folder / "report.json").is_file():
                    raise ValueError("validation directory has no request or report")
                legacy_report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
                if (not isinstance(legacy_report, dict) or legacy_report.get("status") not in {"PASSED", "FAILED"}
                        or not (folder / "output.txt").is_file()):
                    raise ValueError("legacy validation report incomplete or invalid")
                legacy.append({**row, "status": "LEGACY_UNVERIFIED"})
                continue
            request = _read(folder / "request.json")
            _check_request(request, identity, folder.name)
            belongs = request["head"] == head
            row.update({key: request[key] for key in ("head", "actor", "command", "workspace",
                                                      "timeout", "started_at", "claim_fingerprint")})
            from . import validation_execution
            if (folder / "execution.json").exists():
                validation_execution.descriptor(folder, request)
            if (folder / "execution-stop.json").exists():
                validation_execution.stop_record(folder, request)
            if not (folder / "receipt.json").exists():
                # An interrupt may occur after publishing a report. Recovery
                # preserves partial progress but must never launder corrupt JSON.
                if (folder / "report.json").exists():
                    partial = json.loads((folder / "report.json").read_text(encoding="utf-8"))
                    if not isinstance(partial, dict) or partial.get("status") not in {"PASSED", "FAILED"}:
                        raise ValueError("invalid incomplete validation report")
                if (folder / "promotion.json").exists():
                    promotion = json.loads((folder / "promotion.json").read_text(encoding="utf-8"))
                    if (not isinstance(promotion, dict) or promotion.get("status") != "PROMOTION_BLOCKED"
                            or not (folder / "report.json").exists() or partial["status"] != "PASSED"):
                        raise ValueError("invalid incomplete promotion record")
                row.update(status="INCOMPLETE", next_action="inspect_original_execution_and_preserve_run_files")
            else:
                receipt = _read(folder / "receipt.json")
                outcome = receipt.get("status")
                names = {"report.json", "output.txt"}
                if outcome == "PROMOTION_BLOCKED":
                    names.add("promotion.json")
                files = receipt.get("files")
                version = receipt.get("schema_version")
                if version == 2:
                    names.update(name for name in ("execution.json", "execution-stop.json") if (folder / name).exists())
                if (version not in {1, 2} or receipt.get("id") != identity
                        or receipt.get("run_id") != folder.name
                        or receipt.get("request_sha256") != request["sha256"]
                        or outcome not in {"PASSED", "FAILED", "PROMOTION_BLOCKED"}
                        or not isinstance(files, dict) or set(files) != names
                        or any(not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                               for digest in files.values())
                        or receipt.get("process_tree_stopped_verified") is not False):
                    raise ValueError("invalid validation receipt binding")
                row.update(status=outcome, completed_at=receipt["completed_at"])
                if version == 2:
                    from . import validation_execution
                    required, verified = receipt.get("execution_stop_required"), receipt.get("execution_stop_verified")
                    if type(required) is not bool or type(verified) is not bool:
                        raise ValueError("invalid execution stop flags")
                    if "execution.json" in files:
                        validation_execution.descriptor(folder, request)
                    if "execution-stop.json" in files:
                        stop = validation_execution.stop_record(folder, request)
                        if verified != stop["execution_stop_verified"]:
                            raise ValueError("inconsistent execution stop receipt")
                    elif verified:
                        raise ValueError("execution stop proof missing")
                    row.update(execution_stop_verified=verified, execution_stop_required=required)
                    if required and not verified:
                        row.update(status="STOP_UNVERIFIED", next_action="inspect_original_execution_before_recovery")
                if belongs and verify_artifacts:
                    for name, digest in files.items():
                        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest:
                            raise ValueError("validation evidence changed")
                    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
                    if (not isinstance(report, dict) or report.get("status") not in {"PASSED", "FAILED"}
                            or (outcome in {"PASSED", "PROMOTION_BLOCKED"}) != (report["status"] == "PASSED")):
                        raise ValueError("inconsistent validation test result")
                    row.update(test_status=report["status"], tests_run=report.get("tests_run", 0),
                               exit_code=report.get("exit_code"), error=report.get("error"),
                               evidence=[{"path": str(folder / name), "sha256": digest} for name, digest in files.items()])
            if (folder / "recovery.json").exists():
                recovered = _check_recovery(folder, request)
                if row["status"] not in {"INCOMPLETE", "STOP_UNVERIFIED"}:
                    raise ValueError("recovery cannot replace a completed validation result")
                row.update(status="RECOVERED_STOPPED", recovery=str(folder / "recovery.json"),
                           execution_stop_verified=True, recovery_actor=recovered["actor"],
                           next_action="inspect_handoff_and_existing_lease_before_claiming")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            row.update(status="DAMAGED", error=str(exc), next_action="inspect_validation_evidence_do_not_overwrite")
        if row["status"] in {"INCOMPLETE", "DAMAGED", "STOP_UNVERIFIED"}:
            blocked.append(row)
        if belongs:
            runs.append(row)
        else:
            historical_count += 1
    return {"id": identity, "head": head, "runs": runs, "historical_count": historical_count,
            "legacy_runs": legacy, "blocked_runs": blocked, "handoff_ready": not blocked,
            "current_artifact_hashes_checked": verify_artifacts,
            "process_tree_stopped_verified": False, "execution_started": False}


def require_handoff(root, identity):
    packet = history(root, identity)
    if not packet["handoff_ready"]:
        raise ValueError("validation run incomplete or damaged; inspect validation-runs before recovery")
    return packet


_RUN_FILES = ("request.json", "execution.json", "execution-stop.json", "output.txt",
              "report.json", "promotion.json", "receipt.json")


def _file_snapshot(folder):
    return {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
            for name in _RUN_FILES if (folder / name).exists()}


def _check_recovery(folder, request):
    from . import validation_execution
    execution = validation_execution.descriptor(folder, request)
    if (folder / "execution-stop.json").exists():
        validation_execution.stop_record(folder, request)
    row = _read(folder / "recovery.json")
    if (row.get("schema_version") != 1 or row.get("id") != request["id"]
            or row.get("run_id") != request["run_id"] or row.get("request_sha256") != request["sha256"]
            or row.get("execution_sha256") != execution["sha256"]
            or row.get("status") != "RECOVERED_STOPPED" or row.get("observation") != "JOB_ABSENT"
            or row.get("stop_scope") != "owned_windows_job" or row.get("execution_stop_verified") is not True
            or row.get("process_tree_stopped_verified") is not False
            or not isinstance(row.get("actor"), str) or not row["actor"].strip()
            or not isinstance(row.get("note"), str) or not row["note"].strip()
            or not isinstance(row.get("evidence"), list) or not row["evidence"]
            or row.get("files") != _file_snapshot(folder)):
        raise ValueError("invalid recovery binding or changed run files")
    for item in row["evidence"]:
        if hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("recovery evidence changed")
    return row


def recover(root, identity, run_id, *, actor=None, note=None, evidence=None, apply=False):
    """Preview by default. No process kill, implicit retry, lease release or promotion."""
    from . import validation_execution
    from .battle_orders import _task_writer_lock
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("valid run-id required")
    folder = improvement._folder(root, identity) / "runs" / run_id

    def inspect():
        packet = history(root, identity)
        # Read and validate the specific run even if its stage is now historical.
        request = _read(folder / "request.json")
        _check_request(request, identity, run_id)
        if (folder / "recovery.json").exists():
            row = _check_recovery(folder, request)
            if any(item["run_id"] == run_id for item in packet["blocked_runs"]):
                raise ValueError("recovered run is damaged; preserve original records")
            return request, None, {**row, "already_recovered": True, "applied": False}
        target = next((item for item in packet["blocked_runs"] if item["run_id"] == run_id), None)
        if target is None or target["status"] not in {"INCOMPLETE", "STOP_UNVERIFIED"}:
            raise ValueError("only intact incomplete or stop-unverified runs can be recovered")
        execution = validation_execution.descriptor(folder, request)
        observed = validation_execution.observe_absence(execution)
        return request, execution, {"id": identity, "run_id": run_id,
            "head": packet["head"], "status": "READY_FOR_RECOVERY", **observed,
            "applied": False, "execution_started": False, "lease_released": False,
            "next_action": "review_external_side_effects_then_apply_with_actor_note_and_evidence"}

    if not apply:
        return inspect()[2]
    if not isinstance(actor, str) or not actor.strip() or not isinstance(note, str) or not note.strip():
        raise ValueError("recovery apply requires actor and side-effect review note")
    # Same lock order as validation; a live validator is never recovered underneath it.
    with _task_writer_lock(Path(root), "improvement-validation-" + identity):
        request, execution, preview = inspect()
        if preview.get("already_recovered"):
            return preview
        proofs = improvement._evidence(evidence)
        files = _file_snapshot(folder)
        with _task_writer_lock(Path(root), "improvement-handoff"):
            if improvement.status(root, identity)["events"][-1]["sha256"] != preview["head"]:
                raise ValueError("proposal changed during recovery review")
            # Hashing may take time. Recheck execution absence and all evidence.
            observed = validation_execution.observe_absence(execution)
            if files != _file_snapshot(folder) or proofs != improvement._evidence(evidence):
                raise ValueError("recovery evidence changed during review")
            row = _signed({"schema_version": 1, "id": identity, "run_id": run_id,
                "request_sha256": request["sha256"], "execution_sha256": execution["sha256"],
                "head": preview["head"], "status": "RECOVERED_STOPPED", **observed,
                "actor": actor, "note": note, "evidence": proofs, "files": files,
                "recovered_at": datetime.now(timezone.utc).isoformat()})
            improvement._publish_new(folder / "recovery.json", row)
        return {**row, "applied": True, "execution_started": False, "lease_released": False,
                "next_action": "inspect_handoff_and_existing_lease_before_claiming"}
