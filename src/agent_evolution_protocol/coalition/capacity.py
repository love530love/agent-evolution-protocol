"""Per-agent capacity ledger with fail-closed freshness.

One JSON file per agent under ``coordination/coalition/capacity/``.  A record
is only evidence that an exhaustion *claim* was made at a time by a named
source: it is never proof that quota is intact.  Missing, corrupt, mismatched
or expired records resolve to ``UNKNOWN`` -- never ``AVAILABLE``.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import re

from . import io

SCHEMA = "aep-agent-capacity-v1"
DIRECTORY = "coalition/capacity"
RECORDED_STATES = ("AVAILABLE", "LOW", "EXHAUSTED")
STATES = RECORDED_STATES + ("UNKNOWN",)
RUNNABLE_STATES = ("AVAILABLE", "LOW")

_AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_VENDOR = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/+-]{0,119}$")


def _directory(coord_root):
    return Path(coord_root) / DIRECTORY


def _record_path(coord_root, agent):
    return _directory(coord_root) / f"{agent}.json"


def _clean_agent(value):
    if not isinstance(value, str) or not _AGENT.fullmatch(value.strip()):
        raise ValueError("INVALID_AGENT")
    return value.strip()


def _clean_vendor(value):
    if not isinstance(value, str) or not _VENDOR.fullmatch(value.strip()):
        raise ValueError("INVALID_VENDOR")
    return value.strip()


def _clean_evidence(evidence):
    if isinstance(evidence, str):
        items = [evidence]
    elif isinstance(evidence, (list, tuple)):
        items = list(evidence)
    else:
        raise ValueError("EVIDENCE_REQUIRED")
    cleaned = [item.strip() for item in items
               if isinstance(item, str) and item.strip()]
    if not cleaned:
        raise ValueError("EVIDENCE_REQUIRED")
    return cleaned


def _clean_optional_fraction(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("REMAINING_FRACTION_MUST_BE_NUMBER")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError("REMAINING_FRACTION_OUT_OF_RANGE")
    return round(float(value), 6)


def _clean_optional_seconds(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("REMAINING_SECONDS_MUST_BE_NUMBER")
    if float(value) < 0:
        raise ValueError("REMAINING_SECONDS_NEGATIVE")
    return round(float(value), 3)


def record(coord_root, *, agent, vendor, state, source, evidence,
           remaining_fraction=None, remaining_seconds=None, resets_at=None,
           ttl_minutes=60, now=None):
    """Write one capacity observation.  Only a named source may claim a state."""
    agent = _clean_agent(agent)
    vendor = _clean_vendor(vendor)
    if state not in RECORDED_STATES:
        raise ValueError("INVALID_STATE")
    if not isinstance(source, str) or not _SOURCE.fullmatch(source.strip()):
        raise ValueError("SOURCE_REQUIRED")
    evidence = _clean_evidence(evidence)
    if isinstance(ttl_minutes, bool) or not isinstance(ttl_minutes, int):
        raise ValueError("TTL_MINUTES_MUST_BE_INTEGER")
    if not 1 <= ttl_minutes <= 1440:
        raise ValueError("TTL_MINUTES_OUT_OF_RANGE")
    observed_at = io.coerce_utc(now or datetime.now().astimezone(), "now")
    expires_at = observed_at + timedelta(minutes=ttl_minutes)
    resets = io.coerce_utc(resets_at, "resets_at") if resets_at else None
    payload = {
        "schema": SCHEMA,
        "agent": agent,
        "vendor": vendor,
        "state": state,
        "source": source.strip(),
        "evidence": evidence,
        "observed_at": observed_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "ttl_minutes": ttl_minutes,
        "remaining_fraction": _clean_optional_fraction(remaining_fraction),
        "remaining_seconds": _clean_optional_seconds(remaining_seconds),
        "resets_at": resets.isoformat() if resets else None,
    }
    io.atomic_write_json(_record_path(coord_root, agent), payload)
    return dict(payload)


def read(coord_root, agent, *, now=None):
    """Resolve one agent's capacity to an effective, freshness-checked row."""
    agent = _clean_agent(agent)
    at = io.coerce_utc(now or datetime.now().astimezone(), "now")
    path = _record_path(coord_root, agent)
    try:
        row = io.read_json(path)
    except (OSError, ValueError):
        return _unknown(agent, "CORRUPT_RECORD")
    if row is None:
        return _unknown(agent, "NO_EVIDENCE")
    if not isinstance(row, dict) or row.get("schema") != SCHEMA:
        return _unknown(agent, "UNKNOWN_RECORD_SCHEMA")
    if row.get("agent") != agent:
        return _unknown(agent, "AGENT_MISMATCH")
    if row.get("state") not in RECORDED_STATES:
        return _unknown(agent, "INVALID_RECORDED_STATE")
    expires = io.parse_utc_or_none(row.get("expires_at"))
    if expires is None:
        return _unknown(agent, "EXPIRY_UNREADABLE")
    if expires <= at:
        result = _unknown(agent, "EVIDENCE_EXPIRED")
        result["recorded_state"] = row.get("state")
        result["expired_at"] = expires.isoformat()
        return result
    observed = io.parse_utc_or_none(row.get("observed_at"))
    if observed is None:
        return _unknown(agent, "OBSERVATION_UNREADABLE")
    result = {
        "schema": SCHEMA,
        "agent": agent,
        "vendor": row.get("vendor"),
        "state": row.get("state"),
        "fresh": True,
        "reason": "FRESH_EVIDENCE",
        "source": row.get("source"),
        "evidence": list(row.get("evidence") or []),
        "observed_at": observed.isoformat(),
        "expires_at": expires.isoformat(),
        "remaining_fraction": row.get("remaining_fraction"),
        "remaining_seconds": row.get("remaining_seconds"),
        "resets_at": row.get("resets_at"),
        "runnable": row.get("state") in RUNNABLE_STATES,
    }
    return result


def _unknown(agent, reason):
    return {
        "schema": SCHEMA,
        "agent": agent,
        "vendor": None,
        "state": "UNKNOWN",
        "fresh": False,
        "reason": reason,
        "source": None,
        "evidence": [],
        "observed_at": None,
        "expires_at": None,
        "remaining_fraction": None,
        "remaining_seconds": None,
        "resets_at": None,
        "runnable": False,
    }


def status(coord_root, *, agents=None, now=None):
    """Resolve every requested agent (default: every record on disk)."""
    if agents is None:
        agents = sorted(path.stem for path in _directory(coord_root).glob("*.json")
                        if _AGENT.fullmatch(path.stem))
    at = io.coerce_utc(now or datetime.now().astimezone(), "now")
    rows = {agent: read(coord_root, agent, now=at) for agent in sorted(set(agents))}
    counts = {state: 0 for state in STATES}
    for row in rows.values():
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {"schema": SCHEMA, "at": at.isoformat(), "agents": rows,
            "counts": counts}
