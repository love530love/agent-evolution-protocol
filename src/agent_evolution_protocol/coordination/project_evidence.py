"""Advisory project evidence, sealed for a bounded time.

A takeover PASS from another project is useful context, not a permission.  This
module gives that context a small, reproducible envelope: who observed what,
against which handoff and code fingerprint, when, and for how long the statement
may be quoted.  Every receipt says ``advisory_only`` and
``automatic_takeover_allowed: false``; nothing here can widen a caller's rights.

Freshness is deliberately three-valued.  A receipt that is old is ``STALE`` --
it may still describe a real observation, but not the project's current state.
A receipt that cannot be checked is ``HOLD``.  Only a current, intact receipt
whose source reported ``PASS`` is ``PASS``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from agent_evolution_protocol.coordination.foundation import read_json, write_json_atomic
from agent_evolution_protocol.isotime import parse_iso, to_utc

SCHEMA = "aep-project-evidence-v1"
STATUS_SCHEMA = "aep-project-evidence-status-v1"
PASS = "PASS"
STALE = "STALE"
HOLD = "HOLD"
TAKEOVER_STATUSES = frozenset({PASS, STALE, HOLD})
DEFAULT_MAX_AGE_SECONDS = 900.0
MAX_FUTURE_SKEW_SECONDS = 60.0
_WORKSPACE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_REQUIRED = ("source", "repo_root", "handoff_id", "handoff_hash",
             "takeover_status", "observed_at")


def _utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(payload: dict[str, Any], *, exclude: tuple[str, ...]) -> str:
    body = {key: value for key, value in payload.items() if key not in exclude}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _workspace_id(value: str) -> str:
    text = str(value or "").strip()
    if not _WORKSPACE.fullmatch(text):
        raise ValueError("workspace_id must match [A-Za-z0-9._-]{1,64}")
    return text


def _repo_root(value: str) -> str:
    text = str(value or "").strip()
    if not text or not Path(text).is_absolute():
        raise ValueError("repo_root must be an absolute path")
    return text.replace("\\", "/").rstrip("/")


def seal(raw: dict[str, Any], *, workspace_id: str, task_id: str = "",
         max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
         now: datetime | None = None) -> dict[str, Any]:
    """Copy the source facts into a fresh, hash-sealed advisory receipt."""
    if not isinstance(raw, dict):
        raise ValueError("raw evidence must be an object")
    missing = [field for field in _REQUIRED if not str(raw.get(field, "")).strip()]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")
    if float(max_age_seconds) <= 0:
        raise ValueError("max_age_seconds must be positive")
    observed = parse_iso(str(raw["observed_at"]))
    if observed.tzinfo is None:
        raise ValueError("observed_at must carry a timezone")
    observed = observed.astimezone(timezone.utc)
    sealed_at = _utc_now(now)
    if observed > sealed_at + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS):
        raise ValueError("observed_at is unreasonably far in the future")
    takeover_status = str(raw["takeover_status"]).strip().upper()
    if takeover_status not in TAKEOVER_STATUSES:
        raise ValueError("takeover_status must be PASS, STALE or HOLD")
    if raw.get("automatic_takeover_allowed") is True:
        raise ValueError("project evidence can never grant automatic takeover")
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "workspace_id": _workspace_id(workspace_id),
        "task_id": str(task_id or "").strip(),
        "source": str(raw["source"]).strip(),
        "repo_root": _repo_root(str(raw["repo_root"])),
        "git_head": str(raw.get("git_head", "")).strip(),
        "handoff_id": str(raw["handoff_id"]).strip(),
        "handoff_hash": str(raw["handoff_hash"]).strip(),
        "takeover_status": takeover_status,
        "code_fingerprint": raw.get("code_fingerprint"),
        "architecture_snapshot_hash": str(raw.get("architecture_snapshot_hash", "")).strip(),
        "observed_at": _iso(observed),
        "sealed_at": _iso(sealed_at),
        "max_age_seconds": float(max_age_seconds),
        "expires_at": _iso(observed + timedelta(seconds=float(max_age_seconds))),
        "advisory_only": True,
        "authority": "advisory",
        "automatic_takeover_allowed": False,
        "note": ("Observed project state only. This receipt grants no permission, "
                 "lease, or takeover right and must not be quoted after expiry."),
    }
    receipt["evidence_id"] = "PE-" + _digest(
        receipt, exclude=("receipt_hash",))[:12]
    receipt["receipt_hash"] = _digest(receipt, exclude=("receipt_hash",))
    return receipt


def verify(receipt: dict[str, Any], *, now: datetime | None = None,
           max_age_seconds: float | None = None) -> dict[str, Any]:
    """Check integrity and freshness without trusting the displayed status."""
    reasons: list[str] = []
    if not isinstance(receipt, dict):
        return {"valid": False, "status": HOLD, "reasons": ["receipt_not_object"]}
    if receipt.get("schema") != SCHEMA:
        reasons.append("schema_mismatch")
    if receipt.get("advisory_only") is not True:
        reasons.append("not_advisory")
    if receipt.get("authority") != "advisory":
        reasons.append("authority_not_advisory")
    if receipt.get("automatic_takeover_allowed") is not False:
        reasons.append("automatic_takeover_forbidden")
    stored_hash = str(receipt.get("receipt_hash", ""))
    if not stored_hash or stored_hash != _digest(receipt, exclude=("receipt_hash",)):
        reasons.append("receipt_hash_mismatch")
    expected_id = "PE-" + _digest(
        receipt, exclude=("evidence_id", "receipt_hash"))[:12]
    if receipt.get("evidence_id") != expected_id:
        reasons.append("evidence_id_mismatch")
    if receipt.get("takeover_status") not in TAKEOVER_STATUSES:
        reasons.append("takeover_status_invalid")
    try:
        observed = parse_iso(str(receipt.get("observed_at", "")))
        sealed_at = parse_iso(str(receipt.get("sealed_at", "")))
        expires = parse_iso(str(receipt.get("expires_at", "")))
        if observed.tzinfo is None or sealed_at.tzinfo is None or expires.tzinfo is None:
            reasons.append("timestamp_without_timezone")
    except ValueError:
        reasons.append("timestamp_invalid")
        observed = sealed_at = expires = None
    current = _utc_now(now)
    if observed is not None and expires is not None:
        observed = observed.astimezone(timezone.utc)
        expires = expires.astimezone(timezone.utc)
        if observed > current + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS):
            reasons.append("observed_in_future")
        try:
            limit = float(max_age_seconds if max_age_seconds is not None
                          else receipt.get("max_age_seconds", 0))
        except (TypeError, ValueError):
            limit = 0.0
        if limit <= 0:
            reasons.append("max_age_invalid")
        else:
            try:
                declared_limit = float(receipt.get("max_age_seconds", 0))
            except (TypeError, ValueError):
                declared_limit = 0.0
            if declared_limit <= 0:
                reasons.append("max_age_invalid")
            elif expires != observed + timedelta(seconds=declared_limit):
                reasons.append("expires_at_mismatch")
            if current > observed + timedelta(seconds=limit):
                reasons.append("expired")
    if receipt.get("takeover_status") != PASS:
        reasons.append("source_takeover_not_pass")
    status = HOLD if reasons else PASS
    if reasons == ["expired"]:
        status = STALE
    return {
        "valid": status == PASS,
        "status": status,
        "reasons": reasons,
        "evidence_id": str(receipt.get("evidence_id", "")),
        "workspace_id": str(receipt.get("workspace_id", "")),
        "observed_at": str(receipt.get("observed_at", "")),
        "expires_at": str(receipt.get("expires_at", "")),
        "checked_at": _iso(current),
    }


def path(coord_root: Path, workspace_id: str, evidence_id: str) -> Path:
    return Path(coord_root) / "project_evidence" / _workspace_id(workspace_id) / f"{evidence_id}.json"


def record(coord_root: Path, receipt: dict[str, Any]) -> Path:
    verdict = verify(receipt)
    if not verdict["valid"]:
        raise ValueError(f"refusing to store invalid evidence: {', '.join(verdict['reasons'])}")
    target = path(coord_root, receipt["workspace_id"], receipt["evidence_id"])
    write_json_atomic(target, receipt)
    return target


def latest(coord_root: Path, workspace_id: str, *, now: datetime | None = None,
           max_age_seconds: float | None = None) -> dict[str, Any]:
    safe = _workspace_id(workspace_id)
    directory = Path(coord_root) / "project_evidence" / safe
    rows: list[tuple[datetime, dict[str, Any], dict[str, Any]]] = []
    for file in directory.glob("PE-*.json"):
        receipt = read_json(file)
        if not isinstance(receipt, dict):
            continue
        verdict = verify(receipt, now=now, max_age_seconds=max_age_seconds)
        try:
            observed = parse_iso(str(receipt.get("observed_at", ""))).astimezone(timezone.utc)
        except ValueError:
            observed = datetime.min.replace(tzinfo=timezone.utc)
        rows.append((observed, receipt, verdict))
    rows.sort(key=lambda row: row[0], reverse=True)
    if not rows:
        return {"schema": STATUS_SCHEMA, "workspace_id": safe, "found": False,
                "status": HOLD, "reasons": ["no_receipt"], "receipt": None}
    _, receipt, verdict = rows[0]
    return {"schema": STATUS_SCHEMA, "workspace_id": safe, "found": True,
            "status": verdict["status"], "verdict": verdict, "receipt": receipt}
