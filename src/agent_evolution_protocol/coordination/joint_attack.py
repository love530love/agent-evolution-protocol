"""Event-triggered, local evidence board for a multi-agent operator attack.

This board does not run models, reserve a platform slot, or submit artifacts.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ROLES = {"lead", "deputy", "cross_platform", "catfish", "package_reviewer"}
ADVERSARIAL_ROLES = ("ideator", "falsifier", "replicator", "bias_auditor", "referee")
ADVERSARIAL_REQUIRED = {
    "ideator": ("hypothesis", "predicted_signature", "cheapest_test", "stop_condition"),
    "falsifier": ("counter_hypothesis", "discriminating_test", "predicted_signature", "stop_condition"),
    "replicator": ("protocol", "observed_result", "reproduction_status"),
    "bias_auditor": ("selection_bias", "missing_cases", "audit_conclusion"),
    "referee": ("verdict", "rationale", "residual_risk"),
}


def _id(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError("invalid identifier")
    return value


def _case_dir(root: Path, task: str, submission: str) -> Path:
    return Path(root) / "joint_cases" / _id(task.upper()) / _id(submission)


def _read_object(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON object required: {path}")
    return data


def _write_once(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create: two agents cannot overwrite one another's review.
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=True, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def open_case(root: Path, *, task: str, submission: str, lead: str, deputy: str,
              artifact_sha256: str, result_file: Path) -> dict:
    if lead == deputy:
        raise ValueError("lead and deputy must be different agents")
    for value in (lead, deputy):
        _id(value)
    if not SHA256.fullmatch(artifact_sha256):
        raise ValueError("artifact_sha256 must be 64 lowercase hex characters")
    result = _read_object(result_file)
    if str(result.get("submission_id", "")) != submission:
        raise ValueError("submission_id does not match official result snapshot")
    chips = result.get("chips")
    if not isinstance(chips, dict) or not chips:
        raise ValueError("result snapshot needs a nonempty chips object")
    for chip, row in chips.items():
        _id(chip)
        if not isinstance(row, dict) or row.get("status") not in {"passed", "failed", "pending"}:
            raise ValueError(f"invalid chip result: {chip}")
        if row["status"] == "failed" and not (row.get("raw_error") or row.get("raw_error_path")):
            raise ValueError(f"failed chip needs raw error evidence: {chip}")
    payload = {
        "task": task.upper(), "submission_id": submission, "lead": lead, "deputy": deputy,
        "artifact_sha256": artifact_sha256, "official_snapshot": result,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "state": "DIAGNOSING", "note": "Snapshot is supplied by operator; platform authenticity requires independent verification.",
    }
    _write_once(_case_dir(root, task, submission) / "case.json", payload)
    return payload


def review_case(root: Path, *, task: str, submission: str, agent: str, role: str,
                review_file: Path) -> dict:
    _id(agent)
    if role not in ROLES:
        raise ValueError("invalid review role")
    directory = _case_dir(root, task, submission)
    if not (directory / "case.json").is_file():
        raise ValueError("case not found")
    review = _read_object(review_file)
    required = ("hypothesis", "predicted_signature", "cheapest_test", "stop_condition", "evidence_paths")
    if any(not review.get(field) for field in required):
        raise ValueError("review requires hypothesis, prediction, cheapest test, stop condition and evidence paths")
    if not isinstance(review["evidence_paths"], list):
        raise ValueError("evidence_paths must be a list")
    payload = {"agent": agent, "role": role, "at": datetime.now(timezone.utc).isoformat(), **review}
    _write_once(directory / "reviews" / f"{role}-{agent}.json", payload)
    return payload


def case_status(root: Path, *, task: str, submission: str) -> dict:
    directory = _case_dir(root, task, submission)
    case = _read_object(directory / "case.json")
    reviews = [_read_object(path) for path in sorted((directory / "reviews").glob("*.json"))]
    cross = {item["agent"] for item in reviews if item["role"] == "cross_platform"}
    catfish = {item["agent"] for item in reviews if item["role"] == "catfish"}
    independent = bool(cross and catfish and any(a != b for a in cross for b in catfish))
    gaps = []
    if not cross:
        gaps.append("cross_platform_review_missing")
    if not catfish:
        gaps.append("catfish_challenge_missing")
    if cross and catfish and not independent:
        gaps.append("independent_reviewer_missing")
    return {
        "task": case["task"], "submission_id": submission,
        "lead": case["lead"], "deputy": case["deputy"],
        "review_count": len(reviews), "reviewers": sorted({item["agent"] for item in reviews}),
        "state": "HYPOTHESIS_SPLIT" if independent else "DIAGNOSING",
        "gaps": gaps, "upload_ready": False,
        "next": "Run cheapest falsifiable experiments, cross-chip regression, package review, quota and lease checks; do not infer upload readiness from reviews.",
    }


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _adversarial_path(directory: Path, role: str) -> Path:
    return directory / "adversarial" / f"{role}.json"


def _verified_adversarial(directory: Path) -> tuple[list[dict], list[str]]:
    """Verify the whole frozen chain; a changed review or evidence invalidates successors."""
    accepted: list[dict] = []
    gaps: list[str] = []
    identities: set[str] = set()
    case = _read_object(directory / "case.json")
    for role in ADVERSARIAL_ROLES:
        path = _adversarial_path(directory, role)
        if not path.is_file():
            gaps.append(f"{role}_missing")
            break
        try:
            row = _read_object(path)
            if row.get("role") != role or row.get("case_sha256") != _digest(directory / "case.json"):
                raise ValueError("case binding changed")
            if row.get("previous_sha256") != (_digest(_adversarial_path(directory, accepted[-1]["role"])) if accepted else None):
                raise ValueError("previous review changed")
            agent = row.get("agent")
            if not isinstance(agent, str) or not IDENTIFIER.fullmatch(agent) or agent in identities:
                raise ValueError("reviewer identity is not independent")
            if role in {"falsifier", "replicator", "bias_auditor", "referee"} and agent == case["lead"]:
                raise ValueError("case lead cannot review own proposal")
            for evidence in row.get("evidence", []):
                evidence_path = Path(evidence["path"])
                if not evidence_path.is_file() or _digest(evidence_path) != evidence["sha256"]:
                    raise ValueError("evidence changed or disappeared")
            if not row.get("evidence"):
                raise ValueError("no evidence")
            identities.add(agent)
            accepted.append(row)
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
            gaps.append(f"{role}_invalid: {exc}")
            break
    return accepted, gaps


def submit_adversarial_review(root: Path, *, task: str, submission: str, agent: str,
                              role: str, review_file: Path) -> dict:
    """Append one role's review after independent, evidence-bound predecessors."""
    _id(agent)
    if role not in ADVERSARIAL_ROLES:
        raise ValueError("invalid adversarial role")
    directory = _case_dir(root, task, submission)
    case_path = directory / "case.json"
    if not case_path.is_file():
        raise ValueError("case not found")
    case = _read_object(case_path)
    index = ADVERSARIAL_ROLES.index(role)
    accepted, gaps = _verified_adversarial(directory)
    if len(accepted) != index:
        raise ValueError(f"review order or prior evidence invalid: {gaps}")
    if agent in {row["agent"] for row in accepted}:
        raise ValueError("adversarial roles require distinct agents")
    if index and agent == case["lead"]:
        raise ValueError("case lead cannot review own proposal")
    review = _read_object(review_file)
    if any(not isinstance(review.get(key), str) or not review[key].strip()
           for key in ADVERSARIAL_REQUIRED[role]):
        raise ValueError(f"{role} review missing required fields")
    if role == "replicator" and review["reproduction_status"] not in {"reproduced", "refuted", "inconclusive"}:
        raise ValueError("invalid reproduction_status")
    if role == "referee" and review["verdict"] not in {"advance", "hold", "reject"}:
        raise ValueError("invalid referee verdict")
    paths = review.get("evidence_paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p.strip() for p in paths):
        raise ValueError("evidence_paths must be a nonempty list")
    evidence = []
    for value in paths:
        path = Path(value).resolve()
        if not path.is_file():
            raise ValueError(f"evidence file missing: {path}")
        evidence.append({"path": str(path), "sha256": _digest(path)})
    previous = _adversarial_path(directory, ADVERSARIAL_ROLES[index - 1]) if index else None
    payload = {"role": role, "agent": agent, "at": datetime.now(timezone.utc).isoformat(),
               "case_sha256": _digest(case_path), "previous_sha256": _digest(previous) if previous else None,
               "evidence": evidence, "review": review, "upload_authorized": False}
    _write_once(_adversarial_path(directory, role), payload)
    return payload


def adversarial_status(root: Path, *, task: str, submission: str) -> dict:
    """Read-only review status; even an advance verdict is never upload approval."""
    directory = _case_dir(root, task, submission)
    case = _read_object(directory / "case.json")
    accepted, gaps = _verified_adversarial(directory)
    complete = len(accepted) == len(ADVERSARIAL_ROLES)
    verdict = accepted[-1]["review"]["verdict"] if complete else None
    return {"task": case["task"], "submission_id": submission,
            "state": "REVIEWED" if complete else "REVIEW_INCOMPLETE",
            "accepted_roles": [row["role"] for row in accepted], "reviewers": [row["agent"] for row in accepted],
            "gaps": gaps, "verdict": verdict, "upload_ready": False,
            "note": "Independent review is advisory; official evidence, package review, quota and lease gates remain separate."}
