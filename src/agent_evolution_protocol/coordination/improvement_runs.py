"""Immutable validation receipts for handoffs, not proof of process-tree shutdown."""
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
    receipt = _signed({"schema_version": 1, "id": request["id"], "run_id": request["run_id"],
        "request_sha256": request["sha256"], "status": status,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "files": {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in names},
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
            if not (folder / "receipt.json").exists():
                row.update(status="INCOMPLETE", next_action="inspect_original_execution_and_preserve_run_files")
            else:
                receipt = _read(folder / "receipt.json")
                outcome = receipt.get("status")
                names = {"report.json", "output.txt"}
                if outcome == "PROMOTION_BLOCKED":
                    names.add("promotion.json")
                files = receipt.get("files")
                if (receipt.get("schema_version") != 1 or receipt.get("id") != identity
                        or receipt.get("run_id") != folder.name
                        or receipt.get("request_sha256") != request["sha256"]
                        or outcome not in {"PASSED", "FAILED", "PROMOTION_BLOCKED"}
                        or not isinstance(files, dict) or set(files) != names
                        or any(not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                               for digest in files.values())
                        or receipt.get("process_tree_stopped_verified") is not False):
                    raise ValueError("invalid validation receipt binding")
                row.update(status=outcome, completed_at=receipt["completed_at"])
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
        except (OSError, ValueError, KeyError, TypeError) as exc:
            row.update(status="DAMAGED", error=str(exc), next_action="inspect_validation_evidence_do_not_overwrite")
        if row["status"] in {"INCOMPLETE", "DAMAGED"}:
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
