"""Evidence-frozen, baseline-relative mechanism evolution; never self-promotes."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from .mechanism_audit import METRICS, audit


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def propose(root: Path, metrics_file: Path, *, change: str, rollback: str, author: str) -> dict:
    """Freeze a candidate against its baseline; source file must not change later."""
    if not all(value.strip() for value in (change, rollback, author)):
        raise ValueError("change, rollback and author are required")
    raw = metrics_file.read_bytes()
    assessment = audit(metrics_file)
    if not assessment["trigger_mechanism_challenge"]:
        raise ValueError("no evidenced mechanism-challenge trigger")
    series = json.loads(raw)
    baseline = series[-1]
    for evidence in baseline["evidence_paths"]:
        if not Path(evidence).is_file():
            raise ValueError(f"missing baseline evidence: {evidence}")
    digest = hashlib.sha256(raw).hexdigest()
    identity = hashlib.sha256((digest + "\n" + change.strip()).encode("utf-8")).hexdigest()[:16]
    destination = Path(root) / "evolution" / "proposals" / f"{identity}.json"
    if destination.exists():
        return _read(destination)
    row = {
        "id": identity, "state": "FROZEN_CHALLENGER", "author": author,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": digest, "source_path": str(metrics_file.resolve()),
        "baseline": baseline, "trigger": assessment, "change": change.strip(),
        "rollback": rollback.strip(), "automatic_promotion": False,
    }
    _atomic(destination, row)
    return row


def evaluate(root: Path, proposal_id: str, candidate_file: Path) -> dict:
    """Compare an observed challenger to frozen baseline; return review eligibility only."""
    path = Path(root) / "evolution" / "proposals" / f"{proposal_id}.json"
    proposal = _read(path)
    source = Path(proposal["source_path"])
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != proposal["source_sha256"]:
        raise ValueError("frozen source changed or disappeared; re-propose with new evidence")
    candidate = _read(candidate_file)
    if not isinstance(candidate, dict) or not candidate.get("task") or not candidate.get("evidence_paths"):
        raise ValueError("candidate needs task and evidence_paths")
    for evidence in candidate["evidence_paths"]:
        if not Path(evidence).is_file():
            raise ValueError(f"missing candidate evidence: {evidence}")
    baseline = proposal["baseline"]
    if candidate["task"] == baseline["task"]:
        raise ValueError("candidate must come from a later independent task")
    changes = {}
    for metric, direction in METRICS.items():
        value = candidate.get(metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"invalid candidate {metric}")
        if metric in ("cross_review_coverage", "repeat_error_rate") and value > 1:
            raise ValueError(f"invalid candidate {metric}")
        before = baseline[metric]
        changes[metric] = {"baseline": before, "candidate": value,
                           "improved": value < before if direction == "lower" else value > before,
                           "nonregressed": value <= before if direction == "lower" else value >= before}
    eligible = all(item["nonregressed"] for item in changes.values()) and any(item["improved"] for item in changes.values())
    result = {"proposal_id": proposal_id, "status": "EXPERT_REVIEW" if eligible else "BASELINE_REQUIRED",
              "changes": changes, "candidate_task": candidate["task"],
              "candidate_sha256": hashlib.sha256(candidate_file.read_bytes()).hexdigest(),
              "automatic_promotion": False,
              "reason": "Evaluation is not approval: independent review, rollback rehearsal and explicit adoption are still required."}
    _atomic(Path(root) / "evolution" / "evaluations" / f"{proposal_id}.json", result)
    return result
