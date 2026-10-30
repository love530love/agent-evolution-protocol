"""N-of-M independent attestations before an irreversible action.

A single agent that both performs and approves an upload is not a control, it
is a receipt. This module turns "several agents agreed" into a checkable claim:
every attestation binds to one action hash and one evidence hash, the initiator
cannot approve their own action, duplicates collapse, stale or replayed
attestations are rejected, and any valid rejection wins.

It is a gate, not an executor: it never sends, uploads, publishes or deletes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re


DECISIONS = ("approve", "reject", "abstain")
IRREVERSIBLE_OPERATIONS = frozenset({
    "upload", "publish", "delete", "remove", "transfer", "send", "submit",
    "deploy", "payment", "release", "overwrite", "rotate-credential",
})
DEFAULT_THRESHOLD = {"irreversible": 2, "reversible": 1}
_HEX = re.compile(r"[0-9a-f]{64}")
_AGENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def digest(value):
    """sha256 of a JSON-serializable value, stable across runs."""
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def requires(operation, *, threshold=None):
    """Return the attestation requirement for an operation name."""
    if not isinstance(operation, str) or not operation.strip():
        raise ValueError("operation is required")
    name = operation.strip().lower()
    kind = "irreversible" if name in IRREVERSIBLE_OPERATIONS else "reversible"
    if threshold is None:
        return {"operation": name, "kind": kind, "threshold": DEFAULT_THRESHOLD[kind]}
    if type(threshold) is not int or threshold < 1:
        raise ValueError("threshold must be a positive int")
    return {"operation": name, "kind": kind, "threshold": threshold}


def _hex_digest(value):
    """Return the lower-case digest, or None when the value is not a sha256."""
    if not isinstance(value, str) or not _HEX.fullmatch(value.strip().lower()):
        return None
    return value.strip().lower()


def _moment(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _now(now):
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if isinstance(now, str):
        parsed = _moment(now)
        if parsed is None:
            raise ValueError("now must be an ISO timestamp")
        return parsed
    raise TypeError("now must be a datetime, ISO string or None")


def evaluate(attestations, *, action_sha256, initiator, threshold,
             opened_at=None, max_age_minutes=60, now=None):
    """Decide whether an action has enough independent approval to proceed.

    ``threshold`` counts distinct approving agents, excluding the initiator.
    A rejection from any valid attestation ends the decision immediately; an
    abstention is recorded but never helps reach the threshold.
    """
    if not isinstance(initiator, str) or not _AGENT.fullmatch(initiator.strip()):
        raise ValueError("initiator must be a stable agent ID")
    initiator = initiator.strip()
    if type(threshold) is not int or threshold < 1:
        raise ValueError("threshold must be a positive int")
    if not isinstance(max_age_minutes, (int, float)) or max_age_minutes <= 0:
        raise ValueError("max_age_minutes must be positive")
    if not isinstance(attestations, (list, tuple)):
        raise ValueError("attestations must be a list")

    moment = _now(now)
    opened = _moment(opened_at) if opened_at is not None else None
    if opened_at is not None and opened is None:
        raise ValueError("opened_at must be an ISO timestamp")
    if not isinstance(action_sha256, str) or not _HEX.fullmatch(action_sha256.strip().lower()):
        raise ValueError("action_sha256 must be a sha256 hex digest")
    action = action_sha256.strip().lower()

    approvals, rejections, abstentions, invalid = {}, {}, {}, []
    seen = set()
    for index, row in enumerate(attestations):
        if not isinstance(row, dict):
            invalid.append({"index": index, "agent": "", "reason": "attestation must be an object"})
            continue
        agent = row.get("agent")
        if not isinstance(agent, str) or not _AGENT.fullmatch(agent.strip()):
            invalid.append({"index": index, "agent": str(agent)[:64],
                            "reason": "invalid agent ID"})
            continue
        agent = agent.strip()
        decision = row.get("decision")
        if decision not in DECISIONS:
            invalid.append({"index": index, "agent": agent,
                            "reason": f"decision must be one of {DECISIONS}"})
            continue
        if agent == initiator:
            invalid.append({"index": index, "agent": agent,
                            "reason": "initiator cannot attest to their own action"})
            continue
        bound = row.get("action_sha256")
        if not isinstance(bound, str) or bound.strip().lower() != action:
            invalid.append({"index": index, "agent": agent,
                            "reason": "attestation is bound to a different action"})
            continue
        evidence = _hex_digest(row.get("evidence_sha256"))
        if evidence is None:
            invalid.append({"index": index, "agent": agent,
                            "reason": "evidence_sha256 must be a sha256 hex digest"})
            continue
        at = _moment(row.get("at"))
        if at is None:
            invalid.append({"index": index, "agent": agent, "reason": "at must be an ISO timestamp"})
            continue
        if opened is not None and at < opened:
            invalid.append({"index": index, "agent": agent,
                            "reason": "attestation predates the request"})
            continue
        if (moment - at).total_seconds() > max_age_minutes * 60:
            invalid.append({"index": index, "agent": agent,
                            "reason": f"attestation older than {max_age_minutes} minutes"})
            continue
        if agent in seen:
            invalid.append({"index": index, "agent": agent,
                            "reason": "duplicate attestation from the same agent"})
            continue
        seen.add(agent)
        record = {"agent": agent, "at": at.isoformat(), "evidence_sha256": evidence,
                  "note": str(row.get("note", ""))[:500]}
        if decision == "approve":
            approvals[agent] = record
        elif decision == "reject":
            rejections[agent] = record
        else:
            abstentions[agent] = record

    approved = sorted(approvals)
    rejected = sorted(rejections)
    if rejected:
        status, reason = "REJECTED", "a valid attestation rejected the action"
    elif len(approved) >= threshold:
        status, reason = "APPROVED", "independent approval threshold reached"
    else:
        status, reason = "PENDING", "not enough independent approvals yet"

    fingerprint = digest({"action_sha256": action, "threshold": threshold,
                          "approved": approved, "rejected": rejected}) if status != "PENDING" else ""
    return {
        "schema": "agent-evolution-quorum-v1",
        "action_sha256": action,
        "initiator": initiator,
        "threshold": threshold,
        "status": status,
        "reason": reason,
        "approvals": approved,
        "rejections": rejected,
        "abstentions": sorted(abstentions),
        "remaining": max(0, threshold - len(approved)),
        "invalid": invalid,
        "evidence_sha256": {agent: row["evidence_sha256"] for agent, row in sorted(approvals.items())},
        "decision_fingerprint": fingerprint,
        "checked_at": moment.isoformat(),
        "executed": False,
    }


def required_attestations(operation, *, threshold=None):
    """Convenience wrapper: requirement plus the decision skeleton."""
    need = requires(operation, threshold=threshold)
    return {**need, "schema": "agent-evolution-quorum-requirement-v1",
            "self_attestation_allowed": False, "executed": False}


def main(argv=None):
    """Evaluate a request file: {action_sha256, initiator, threshold, attestations, ...}."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="quorum", description=__doc__.splitlines()[0])
    parser.add_argument("request_file", help="JSON file describing one action and its attestations")
    args = parser.parse_args(argv)
    try:
        from pathlib import Path
        payload = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request file must contain a JSON object")
        result = evaluate(payload.get("attestations", []),
                          action_sha256=payload.get("action_sha256"),
                          initiator=payload.get("initiator"),
                          threshold=payload.get("threshold", 2),
                          opened_at=payload.get("opened_at"),
                          max_age_minutes=payload.get("max_age_minutes", 60),
                          now=payload.get("now"))
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "APPROVED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
