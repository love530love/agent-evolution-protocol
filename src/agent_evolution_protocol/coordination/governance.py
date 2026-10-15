"""Bounded role contracts and auditable, non-executing intervention proposals.

This module deliberately does not change rules, dispatch work or approve its own
proposals.  A review record is evidence for a later human-governed decision, not
authority to apply one.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4


_IDENTITY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_MAX_TEXT = 4000
_ROLE_CONTRACTS = {
    "orchestrator": {"capabilities": ["coordinate", "propose"], "independent": False},
    "ideator": {"capabilities": ["propose"], "independent": True},
    "catfish": {"capabilities": ["challenge", "propose"], "independent": True},
    "falsifier": {"capabilities": ["challenge", "design_discriminating_test"], "independent": True},
    "bias_auditor": {"capabilities": ["audit_bias"], "independent": True},
    "replicator": {"capabilities": ["reproduce"], "independent": True},
    "referee": {"capabilities": ["judge_evidence"], "independent": True},
    "gatekeeper": {"capabilities": ["audit_claims"], "independent": True},
    "archivist": {"capabilities": ["curate_memory"], "independent": True},
}
_TIERS = {
    "read_only": "L0",
    "implementation": "L1",
    "metric_change": "L2",
    "role_change": "L2",
    "dispatch_change": "L2",
    "evidence_change": "L2",
    "verdict_override": "L2",
    "charter_change": "L3",
    "safety_override": "L3",
}


def roles() -> dict:
    """Return descriptive roles, not credentials or live agent assignments."""
    return deepcopy(_ROLE_CONTRACTS)


def assess_intervention(request: dict) -> dict:
    """Classify a proposal. No status from this function authorizes execution."""
    if not isinstance(request, dict):
        raise ValueError("intervention must be an object")
    if set(request) - {"type", "by", "target", "rationale", "evidence_paths", "reviewers", "after"}:
        raise ValueError("unknown intervention fields")
    try:
        encoded = json.dumps(request, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("intervention must be finite JSON data") from exc
    if len(encoded) > 16384:
        raise ValueError("intervention exceeds 16 KiB")
    kind = request.get("type")
    actor = request.get("by")
    target = request.get("target")
    rationale = request.get("rationale", "")
    evidence = request.get("evidence_paths", [])
    reviewers = request.get("reviewers", [])
    if not isinstance(kind, str) or kind not in _TIERS:
        raise ValueError("unknown intervention type")
    if not isinstance(actor, str) or not _IDENTITY.fullmatch(actor):
        raise ValueError("invalid actor")
    if not isinstance(target, str) or not target.strip() or len(target) > 240:
        raise ValueError("target is required (max 240 characters)")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > _MAX_TEXT:
        raise ValueError("rationale is required (max 4000 characters)")
    if not isinstance(evidence, list) or len(evidence) > 20 or any(
        not isinstance(item, str) or not item.strip() for item in evidence
    ):
        raise ValueError("evidence_paths must be a list of nonempty paths")
    if not isinstance(reviewers, list) or len(reviewers) > 12 or any(
        not isinstance(item, str) or not _IDENTITY.fullmatch(item) for item in reviewers
    ):
        raise ValueError("reviewers must be valid agent identities")
    if len(set(reviewers)) != len(reviewers) or actor in reviewers:
        raise ValueError("reviewers must be unique and independent of author")
    if "after" in request and not isinstance(request["after"], dict):
        raise ValueError("after must be a declarative object")
    tier = _TIERS[kind]
    if tier in {"L2", "L3"} and not evidence:
        raise ValueError("high-risk intervention requires evidence_paths")
    if tier == "L0":
        status = "RECORDED_READ_ONLY"
    elif tier == "L1":
        status = "REVIEW_REQUIRED"
    else:
        status = "QUARANTINED"
    return {
        "tier": tier,
        "status": status,
        "automatic_application": False,
        "required_review": "independent_review" if tier == "L1" else (
            "independent_expert_review_and_explicit_authorization" if tier == "L2" else
            "formal_charter_review_and_explicit_authorization" if tier == "L3" else "none"
        ),
    }


def submit_intervention(root: Path, request: dict) -> dict:
    """Freeze a proposal and evidence digests in an exclusively created file."""
    decision = assess_intervention(request)
    evidence = []
    for raw in request.get("evidence_paths", []):
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"missing evidence: {raw}")
        evidence.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    row = {
        "id": uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "request": deepcopy(request),
        "evidence": evidence,
        **decision,
    }
    directory = Path(root) / "governance" / "interventions"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{row['id']}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(row, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return row


def read_interventions(root: Path) -> list[dict]:
    """Read immutable proposal snapshots; no review or adoption is inferred."""
    directory = Path(root) / "governance" / "interventions"
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]
