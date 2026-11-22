"""Per-agent quota and usage envelopes for quota-aware command relay.

A vendor limit is a property of an agent's *current* operating window, not a
permanent capability.  This module records that window explicitly -- remaining
units, a reserve that must not be consumed by ordinary work, a reset time and
the evidence for the declaration -- so successor selection never has to guess
from a stale chat report.

The module is read/write for its own files only.  It never wakes a model,
touches a desktop, calls a provider API or authorises an upload.  A declared
quota is self-reported evidence; it is not an ACK and not independent proof of
remaining provider balance.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

SCHEMA = "aep-quota-envelope-v1"
AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
PROVIDER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
CAPABILITY = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
UNITS = ("credits", "requests", "tokens", "minutes", "percent", "unknown")
SOURCES = ("manual", "provider_api", "screenshot", "hook", "self_report")
STATES = ("ACTIVE", "EXHAUSTED", "BLOCKED", "EXPIRED", "UNKNOWN", "INVALID")

NOTE = (
    "Declared remaining quota is self-reported evidence, not a provider balance "
    "proof, not an ACK and not a wake or execution claim."
)


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError("now must be a datetime or None")
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
            else value.astimezone(timezone.utc))


def _iso(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _read(path: Path) -> dict | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_write(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _clean_names(values, label: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)) or len(values) > 32:
        raise ValueError(f"{label} must be a bounded list")
    cleaned = []
    for value in values:
        if not isinstance(value, str) or not CAPABILITY.fullmatch(value.strip()):
            raise ValueError(f"{label} contains an invalid token")
        cleaned.append(value.strip())
    return sorted(set(cleaned))


def _clean_evidence(values) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)) or not values or len(values) > 20:
        raise ValueError("EVIDENCE_REQUIRED")
    cleaned = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > 1000:
            raise ValueError("EVIDENCE_REQUIRED")
        cleaned.append(value.strip())
    return cleaned


def _number(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative int")
    return value


def declare(root, *, agent: str, provider: str, unit: str, remaining: int,
            reserve: int = 0, limit: int | None = None, reset_at: str = "",
            source: str = "manual", evidence, capabilities=None,
            subagent_children: int | None = None, subagent_units: int | None = None,
            ttl_minutes: int = 60, now: datetime | None = None) -> dict:
    """Write one explicit quota envelope for an agent.

    ``remaining`` counts the current window including ``reserve``.  The
    delegable amount is ``max(0, remaining - reserve)``; the reserve exists so a
    role can still spend a little on a handoff instead of stalling at zero.
    """
    if not isinstance(agent, str) or not AGENT.fullmatch(agent):
        raise ValueError("invalid agent")
    if not isinstance(provider, str) or not PROVIDER.fullmatch(provider.strip()):
        raise ValueError("invalid provider")
    provider = provider.strip()
    if unit not in UNITS:
        raise ValueError(f"unit must be one of {UNITS}")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    remaining = _number(remaining, "remaining")
    reserve = _number(reserve, "reserve")
    if limit is not None:
        limit = _number(limit, "limit")
        if remaining > limit:
            raise ValueError("remaining cannot exceed limit")
        if reserve > limit:
            raise ValueError("reserve cannot exceed limit")
    if unit == "percent" and (remaining > 100 or reserve > 100 or (limit is not None and limit > 100)):
        raise ValueError("percent values must be 0..100")
    if not 1 <= ttl_minutes <= 1440:
        raise ValueError("TTL must be 1..1440 minutes")
    evidence = _clean_evidence(evidence)
    capabilities = _clean_names(capabilities, "capabilities")
    reset = ""
    if reset_at:
        try:
            reset = _iso(reset_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("reset_at must be an ISO timestamp") from exc
    subagent = None
    if subagent_children is not None or subagent_units is not None:
        children = 0 if subagent_children is None else _number(subagent_children, "subagent_children")
        units = 0 if subagent_units is None else _number(subagent_units, "subagent_units")
        if children > 20:
            raise ValueError("subagent_children must be <= 20")
        subagent = {"max_children": children, "max_units": units}
    moment = _utc(now)
    payload = {
        "schema": SCHEMA,
        "agent": agent,
        "provider": provider,
        "unit": unit,
        "limit": limit,
        "remaining": remaining,
        "reserve": reserve,
        "reset_at": reset,
        "source": source,
        "evidence": evidence,
        "capabilities": capabilities,
        "subagent": subagent,
        "blocked": False,
        "blocked_reason": "",
        "blocked_at": "",
        "declared_at": moment.isoformat(),
        "expires_at": (moment + timedelta(minutes=ttl_minutes)).isoformat(),
        "note": NOTE,
    }
    _atomic_write(_path(root, agent), payload)
    return effective(payload, moment)


def _path(root, agent: str) -> Path:
    return Path(root) / "relay" / "resources" / f"{agent}.json"


def load(root, agent: str) -> dict | None:
    if not isinstance(agent, str) or not AGENT.fullmatch(agent):
        raise ValueError("invalid agent")
    return _read(_path(root, agent))


def mark_exhausted(root, *, agent: str, reason: str, evidence, source: str = "self_report",
                   now: datetime | None = None) -> dict:
    """Record an explicit limit hit without inventing a numeric balance.

    The existing envelope is retained (if any); ``blocked`` is the operator's
    or agent's declaration that ordinary work must stop now.  A later
    ``declare`` clears it by writing a fresh envelope.
    """
    if not isinstance(agent, str) or not AGENT.fullmatch(agent):
        raise ValueError("invalid agent")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ValueError("reason is required")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    evidence = _clean_evidence(evidence)
    moment = _utc(now)
    row = load(root, agent) or {
        "schema": SCHEMA, "agent": agent, "provider": "unknown", "unit": "unknown",
        "limit": None, "remaining": 0, "reserve": 0, "reset_at": "",
        "source": source, "evidence": evidence, "capabilities": [], "subagent": None,
        "declared_at": moment.isoformat(),
        "expires_at": (moment + timedelta(minutes=60)).isoformat(),
    }
    row.update({"blocked": True, "blocked_reason": reason.strip(),
                "blocked_at": moment.isoformat(), "source": source,
                "evidence": evidence, "note": NOTE})
    _atomic_write(_path(root, agent), row)
    return effective(row, moment)


def effective(row: dict | None, at: datetime | None = None) -> dict:
    """Compute the state of one envelope at one instant, never trusting a label."""
    moment = _utc(at)
    if not isinstance(row, dict):
        return {"state": "UNKNOWN", "available": 0, "limit": None, "unit": "unknown",
                "capabilities": [], "subagent": None, "evidence": [],
                "expires_at": "", "reset_at": "", "remaining": 0, "reserve": 0,
                "agent": "", "provider": ""}
    agent = str(row.get("agent", ""))
    provider = str(row.get("provider", ""))
    unit = str(row.get("unit", "unknown"))
    remaining = row.get("remaining", 0)
    reserve = row.get("reserve", 0)
    limit = row.get("limit")
    if (isinstance(remaining, bool) or not isinstance(remaining, int) or remaining < 0
            or isinstance(reserve, bool) or not isinstance(reserve, int) or reserve < 0):
        return {"state": "INVALID", "available": 0, "limit": limit, "unit": unit,
                "capabilities": [], "subagent": None, "evidence": [],
                "expires_at": "", "reset_at": "", "remaining": 0, "reserve": 0,
                "agent": agent, "provider": provider}
    available = max(0, remaining - reserve)
    expires_at = str(row.get("expires_at", ""))
    reset_at = str(row.get("reset_at", ""))
    state = "ACTIVE"
    if row.get("blocked") is True:
        state = "BLOCKED"
    else:
        if expires_at:
            try:
                if datetime.fromisoformat(expires_at) <= moment:
                    state = "EXPIRED"
            except (TypeError, ValueError):
                state = "INVALID"
        if state == "ACTIVE" and available <= 0:
            state = "EXHAUSTED"
    return {
        "agent": agent, "provider": provider, "state": state, "unit": unit,
        "limit": limit, "remaining": remaining, "reserve": reserve,
        "available": available, "capabilities": list(row.get("capabilities", [])),
        "subagent": row.get("subagent"), "evidence": list(row.get("evidence", [])),
        "expires_at": expires_at, "reset_at": reset_at,
        "blocked_reason": str(row.get("blocked_reason", "")),
        "source": str(row.get("source", "")),
    }


def snapshot(root, *, at: datetime | None = None) -> dict:
    folder = Path(root) / "relay" / "resources"
    resources, invalid = [], []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        row = _read(path)
        if row is None:
            invalid.append({"path": str(path), "reason": "UNREADABLE"})
            continue
        item = effective(row, at)
        if item["state"] == "INVALID":
            invalid.append({"path": str(path), "reason": "INVALID_ENVELOPE"})
            continue
        resources.append(item)
    return {"schema": "aep-quota-snapshot-v1", "resources": resources,
            "invalid": invalid, "note": NOTE}


def gate(root, *, agent: str, units: int = 1, children: int = 0,
         allow_unknown: bool = False, now: datetime | None = None) -> dict:
    """Fail-closed check before ordinary delegation or subagent work."""
    units = _number(units, "units")
    children = _number(children, "children")
    item = effective(load(root, agent), now)
    reasons = []
    if item["state"] == "UNKNOWN" and not allow_unknown:
        reasons.append("UNDECLARED")
    elif item["state"] != "ACTIVE":
        reasons.append(item["state"])
    if item["state"] == "ACTIVE" and item["available"] < units:
        reasons.append("INSUFFICIENT_AVAILABLE")
    quota = item.get("subagent") or {}
    if children and int(quota.get("max_children", 0)) < children:
        reasons.append("SUBAGENT_CHILD_BUDGET")
    if units and int(quota.get("max_units", 0)) and units > int(quota["max_units"]):
        reasons.append("SUBAGENT_UNIT_BUDGET")
    return {"agent": agent, "allowed": not reasons, "reasons": reasons,
            "requested_units": units, "requested_children": children,
            "resource": item, "note": "Quota gate only; it does not wake or assign an agent."}


def rank(root, *, capability: str = "", min_units: int = 1,
         exclude=(), now: datetime | None = None) -> list[dict]:
    """Rank declared envelopes for one capability without contacting anyone."""
    if capability and not CAPABILITY.fullmatch(capability):
        raise ValueError("invalid capability")
    excluded = {str(name) for name in (exclude or [])}
    moment = _utc(now)
    rows = []
    for item in snapshot(root, at=moment)["resources"]:
        if item["agent"] in excluded:
            continue
        match = not capability or capability in item["capabilities"]
        quota_score = (min(1.0, item["available"] / max(1, item["limit"]))
                       if item.get("limit") else min(1.0, item["available"] / 50.0))
        evidence_score = min(1.0, len(item["evidence"]) / 3.0)
        freshness = 0.0
        if item["expires_at"]:
            try:
                remaining = (datetime.fromisoformat(item["expires_at"]) - moment).total_seconds()
                freshness = max(0.0, min(1.0, remaining / 3600.0))
            except (TypeError, ValueError):
                freshness = 0.0
        eligible = item["state"] == "ACTIVE" and item["available"] >= min_units and match
        score = round(0.55 * (1.0 if match else 0.0) + 0.25 * quota_score
                      + 0.10 * evidence_score + 0.10 * freshness, 6)
        rows.append({
            "agent": item["agent"], "provider": item["provider"],
            "resource_state": item["state"], "available": item["available"],
            "unit": item["unit"], "capability_match": match,
            "eligible": eligible, "score": score,
            "reasons": [] if eligible else [
                "CAPABILITY_MISMATCH" if not match else item["state"],
                *([] if item["available"] >= min_units else ["INSUFFICIENT_AVAILABLE"]),
            ],
        })
    return sorted(rows, key=lambda row: (-row["score"], row["agent"]))


def fingerprint(row: dict) -> str:
    """Stable digest of the quota facts used in an offer/decision."""
    material = {key: row.get(key) for key in (
        "agent", "provider", "state", "unit", "limit", "remaining", "reserve",
        "available", "capabilities", "expires_at", "reset_at", "blocked_reason",
    )}
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()