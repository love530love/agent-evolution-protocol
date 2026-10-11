"""Event-triggered, local evidence board for a multi-agent operator attack.

This board does not run models, reserve a platform slot, or submit artifacts.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ROLES = {"lead", "deputy", "cross_platform", "catfish", "package_reviewer"}


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
