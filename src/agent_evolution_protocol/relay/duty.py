"""Quota-aware command duty: heartbeat, succession plan and offer/accept.

This layer sits on top of ``federation.command_handoff`` (the single-writer role
state machine) and ``relay.resources`` (explicit quota envelopes).  It adds the
missing operational pieces for a fleet whose providers impose usage, credit or
time limits:

* a heartbeat is a *self-report* with a checkpoint pack, not an ACK;
* a seat whose heartbeat is missing, stale or from a previous tenure is not
  READY even if the state file still names a holder;
* a quota-limited holder can prepare a fenced offer to an eligible successor;
* the successor must explicitly accept with the checkpoint digest, and only
  then does the role change hands;
* acceptance never fabricates a heartbeat -- the new holder must beat;
* offers are deposited into a mailbox and optionally queued for wake.  Neither
  deposit nor wake execution is acceptance.

A holder that loses its heartbeat cannot be replaced through the current
``command_handoff`` API, which accepts only capacity signals.  ``plan`` reports
``EMERGENCY_REQUIRES_UPSTREAM`` instead of inventing a ``no_heartbeat`` signal.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from uuid import uuid4

from ..coordination import conversation_journal
from ..federation import command_handoff
from ..federation.diagnosis import CAPACITY_SIGNALS
from ..federation.locking import workspace_lock
from . import resources

SCHEMA = "aep-command-relay-v1"
HEARTBEAT_SCHEMA = "aep-relay-heartbeat-v1"
PACK_SCHEMA = "aep-relay-checkpoint-v1"
OFFER_SCHEMA = "aep-relay-offer-v1"
ACCEPT_SCHEMA = "aep-relay-acceptance-v1"
DECLINE_SCHEMA = "aep-relay-decline-v1"
TAKEOVER_SCHEMA = "aep-relay-takeover-v1"
SUMMARY_SCHEMA = "aep-command-relay-summary-v1"

FRESH = "FRESH"
STALE = "STALE"
MISSING = "MISSING"
INVALID = "INVALID"
STALE_TENURE = "STALE_TENURE"
HEARTBEAT_STATES = (FRESH, STALE, MISSING, INVALID, STALE_TENURE)

OFFER_ID = re.compile(r"^R[0-9a-f]{19}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
NONCE = re.compile(r"^[A-Za-z0-9_.-]{0,64}$")
EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DEFAULT_HEARTBEAT_MINUTES = 15
MAX_HEARTBEAT_MINUTES = 240
DEFAULT_OFFER_MINUTES = 30
MAX_OFFER_MINUTES = 240

NOTE = (
    "Heartbeat, quota and delivery are self-reported evidence.  A mailbox "
    "deposit, a wake-queue entry or a desktop focus is not target ACK or "
    "acceptance; only a verified accept changes the role."
)


def _canonical(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _digest(payload) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _atomic_write(path, payload):
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
    return path


def _read(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _now(value):
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError("now must be a datetime or None")
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
            else value.astimezone(timezone.utc))


def _parse_time(value):
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp missing")
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _validate_agent(value):
    if not isinstance(value, str) or not resources.AGENT.fullmatch(value):
        raise ValueError("INVALID_AGENT")
    return value


def _validate_role(value):
    if not isinstance(value, str) or not command_handoff.ROLE_NAME.fullmatch(value):
        raise ValueError("INVALID_ROLE")
    return value


def _validate_offer_id(value):
    if not isinstance(value, str) or not OFFER_ID.fullmatch(value):
        raise ValueError("INVALID_OFFER_ID")
    return value


def _validate_sha(value):
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError("INVALID_CHECKPOINT_SHA256")
    return value


def _evidence(value):
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise ValueError("EVIDENCE_REQUIRED")
    cleaned = []
    for item in values:
        if not isinstance(item, str) or not item.strip() or len(item) > 2000:
            raise ValueError("EVIDENCE_REQUIRED")
        cleaned.append(item.strip())
    if not cleaned or len(cleaned) > 20:
        raise ValueError("EVIDENCE_REQUIRED")
    return cleaned


def _text(value, label, *, maximum=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} is required")
    return value.strip()


def _minutes(value, label, default, maximum):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{label} must be 1..{maximum}")
    return value


def _role_lock(root, role):
    return workspace_lock(Path(root) / "relay" / "locks" / f"{role}.lock", timeout=15.0)


def _heartbeat_path(root, role, agent):
    return Path(root) / "relay" / "heartbeats" / role / f"{agent}.json"


def _pack_path(root, sha):
    return Path(root) / "relay" / "packs" / f"{sha}.json"


def _offer_path(root, offer_id):
    return Path(root) / "relay" / "offers" / f"{offer_id}.json"


def _acceptance_path(root, offer_id):
    return Path(root) / "relay" / "acceptances" / f"{offer_id}.json"


def _decline_path(root, offer_id):
    return Path(root) / "relay" / "declines" / f"{offer_id}.json"


def _status(root):
    try:
        return command_handoff.status(Path(root))
    except ValueError as exc:
        raise ValueError(f"ROLE_STATE_UNREADABLE:{exc}") from exc


def _tenure(record):
    if not record.get("holder"):
        return ""
    for handoff in reversed(record.get("handoffs", [])):
        if handoff.get("to") == record["holder"]:
            return str(handoff.get("handoff_id", ""))
    return "claim"


def _missing_heartbeat(role, agent=""):
    return {"state": MISSING, "role": role, "agent": agent, "tenure": "",
            "checkpoint_sha256": "", "beat_at": "", "expires_at": "",
            "age_seconds": None, "remaining_seconds": None,
            "path": "", "note": "No heartbeat on record."}


# --------------------------------------------------------------------------- #
# Checkpoint packs
# --------------------------------------------------------------------------- #
def _checkpoint(value):
    if not isinstance(value, dict):
        raise ValueError("CHECKPOINT_REQUIRED")
    next_step = _text(value.get("next_step"), "checkpoint.next_step", maximum=2000)
    result = {"next_step": next_step}
    for key in ("completed", "artifacts", "pending_operations", "unknowns"):
        items = value.get(key, [])
        if items is None:
            items = []
        if not isinstance(items, (list, tuple)) or len(items) > 64:
            raise ValueError(f"checkpoint.{key} must be a bounded list")
        cleaned = []
        for item in items:
            if isinstance(item, str):
                if len(item) > 2000:
                    raise ValueError(f"checkpoint.{key} item is too long")
                cleaned.append(item.strip())
            elif key == "pending_operations" and isinstance(item, dict):
                if len(_canonical(item)) > 4000:
                    raise ValueError("checkpoint pending operation is too large")
                cleaned.append(item)
            else:
                raise ValueError(f"checkpoint.{key} item must be text")
        result[key] = cleaned
    if not any(result[key] for key in ("completed", "artifacts",
                                       "pending_operations", "unknowns")):
        raise ValueError("CHECKPOINT_TOO_THIN")
    return result


def _store_checkpoint(root, checkpoint, *, role, agent, quota, now):
    normalized = _checkpoint(checkpoint)
    sha = _digest(normalized)
    path = _pack_path(root, sha)
    payload = {
        "schema": PACK_SCHEMA,
        "sha256": sha,
        "role": role,
        "agent": agent,
        "checkpoint": normalized,
        "quota": resources.fingerprint(quota),
        "created_at": now.isoformat(),
        "note": NOTE,
    }
    existing = _read(path) if path.is_file() else None
    if existing is None:
        _atomic_write(path, payload)
    elif existing.get("sha256") != sha or _digest(existing.get("checkpoint")) != sha:
        raise ValueError("CHECKPOINT_PACK_CORRUPT")
    return sha, path, normalized


def _verify_pack(root, sha):
    path = _pack_path(root, sha)
    row = _read(path) if path.is_file() else None
    if row is None:
        raise ValueError("CHECKPOINT_PACK_MISSING")
    if row.get("schema") != PACK_SCHEMA or row.get("sha256") != sha:
        raise ValueError("CHECKPOINT_PACK_INVALID")
    if _digest(row.get("checkpoint")) != sha:
        raise ValueError("CHECKPOINT_PACK_HASH_MISMATCH")
    return row, path


def _checkpoint_brief(checkpoint):
    return {
        "next_step": checkpoint["next_step"],
        "completed": len(checkpoint["completed"]),
        "artifacts": len(checkpoint["artifacts"]),
        "pending_operations": len(checkpoint["pending_operations"]),
        "unknowns": len(checkpoint["unknowns"]),
    }


# --------------------------------------------------------------------------- #
# Quota and heartbeat facts
# --------------------------------------------------------------------------- #
def _quota(root, agent, now):
    row = resources.load(root, agent)
    if row is None:
        return {"state": "UNDECLARED", "available": 0, "unit": "unknown",
                "limit": None, "capabilities": [], "evidence": [], "agent": agent,
                "provider": "", "expires_at": "", "reset_at": "",
                "remaining": 0, "reserve": 0, "subagent": None,
                "blocked_reason": "", "source": ""}
    return resources.effective(row, now)


def heartbeat_status(root, role, agent, *, now=None, tenure=None):
    """Read one heartbeat.  A heartbeat from another tenure never counts."""
    _validate_role(role)
    _validate_agent(agent)
    moment = _now(now)
    if tenure is None:
        state = _status(root)
        record = state["roles"].get(role)
        if record is None:
            raise ValueError(f"UNKNOWN_ROLE:{role}")
        tenure = _tenure(record)
        if record.get("holder") != agent:
            return _missing_heartbeat(role, agent)
    path = _heartbeat_path(root, role, agent)
    row = _read(path) if path.is_file() else None
    if row is None:
        return _missing_heartbeat(role, agent)
    if (row.get("schema") != HEARTBEAT_SCHEMA or row.get("role") != role
            or row.get("agent") != agent):
        return {**_missing_heartbeat(role, agent), "state": INVALID,
                "path": str(path), "note": "Heartbeat identity/schema mismatch."}
    if row.get("tenure") != tenure:
        return {**_missing_heartbeat(role, agent), "state": STALE_TENURE,
                "path": str(path),
                "note": "Heartbeat belongs to an earlier tenure of this role."}
    try:
        beat_at = _parse_time(row.get("beat_at"))
        expires_at = _parse_time(row.get("expires_at"))
    except (TypeError, ValueError):
        return {**_missing_heartbeat(role, agent), "state": INVALID,
                "path": str(path), "note": "Heartbeat timestamps are invalid."}
    state = FRESH if moment <= expires_at else STALE
    return {
        "state": state, "role": role, "agent": agent, "tenure": tenure,
        "checkpoint_sha256": str(row.get("checkpoint_sha256", "")),
        "beat_at": beat_at.isoformat(), "expires_at": expires_at.isoformat(),
        "age_seconds": max(0.0, (moment - beat_at).total_seconds()),
        "remaining_seconds": (expires_at - moment).total_seconds(),
        "path": str(path), "note": NOTE,
    }


def _seat_from_record(root, role, record, now):
    holder = record.get("holder")
    tenure = _tenure(record)
    quota = _quota(root, holder, now) if holder else {
        "state": "UNDECLARED", "available": 0, "unit": "unknown", "limit": None,
        "capabilities": [], "evidence": [], "agent": "", "provider": "",
        "expires_at": "", "reset_at": "", "remaining": 0, "reserve": 0,
        "subagent": None, "blocked_reason": "", "source": "",
    }
    heartbeat = (heartbeat_status(root, role, holder, now=now, tenure=tenure)
                 if holder else _missing_heartbeat(role))
    state = record.get("state", "UNKNOWN")
    if state == command_handoff.STANDBY:
        seat, ready = "VACANT", False
    elif state == command_handoff.TAKING_OVER:
        seat, ready = "TRANSITIONING", False
    elif state in command_handoff.TAKEOVER_STATES:
        seat, ready = state, False
    elif state == command_handoff.ACTIVE:
        if heartbeat["state"] != FRESH:
            seat, ready = "HEARTBEAT_LOST", False
        elif quota["state"] in {"BLOCKED", "EXHAUSTED", "EXPIRED", "INVALID"}:
            seat, ready = "QUOTA_BLOCKED", False
        elif quota["state"] == "UNDECLARED":
            seat, ready = "READY_UNVERIFIED_QUOTA", False
        else:
            seat, ready = "READY", True
    else:
        seat, ready = "UNKNOWN", False
    return {
        "role": role, "role_state": state, "holder": holder, "tenure": tenure,
        "required_capability": record.get("required_capability"),
        "seat_state": seat, "ready": ready, "heartbeat": heartbeat,
        "quota": quota,
    }


def seat_status(root, role, *, now=None):
    _validate_role(role)
    state = _status(root)
    record = state["roles"].get(role)
    if record is None:
        raise ValueError(f"UNKNOWN_ROLE:{role}")
    return _seat_from_record(root, role, record, _now(now))


def beat(root, *, agent, role, evidence, checkpoint, ttl_minutes=None, now=None):
    """Record a holder heartbeat and checkpoint.  Never fabricates readiness."""
    _validate_agent(agent)
    _validate_role(role)
    proof = _evidence(evidence)
    ttl = _minutes(ttl_minutes, "ttl_minutes", DEFAULT_HEARTBEAT_MINUTES,
                   MAX_HEARTBEAT_MINUTES)
    moment = _now(now)
    with _role_lock(root, role):
        state = _status(root)
        record = state["roles"].get(role)
        if record is None:
            raise ValueError(f"UNKNOWN_ROLE:{role}")
        if record.get("holder") != agent:
            raise ValueError("NOT_HOLDER")
        if record.get("state") not in {command_handoff.ACTIVE, command_handoff.LIMITED}:
            raise ValueError(f"ROLE_NOT_ACTIVE:{record.get('state')}")
        tenure = _tenure(record)
        quota = _quota(root, agent, moment)
        sha, pack, _ = _store_checkpoint(root, checkpoint, role=role, agent=agent,
                                         quota=quota, now=moment)
        beat_id = "HB-" + _digest({
            "role": role, "agent": agent, "tenure": tenure, "sha": sha,
            "evidence": proof, "at": moment.isoformat(),
            "nonce": uuid4().hex,
        })[:20]
        payload = {
            "schema": HEARTBEAT_SCHEMA, "beat_id": beat_id, "role": role,
            "agent": agent, "tenure": tenure, "role_state": record.get("state"),
            "checkpoint_sha256": sha, "checkpoint_path": str(pack),
            "evidence": proof, "beat_at": moment.isoformat(),
            "expires_at": (moment + timedelta(minutes=ttl)).isoformat(),
            "ttl_minutes": ttl, "note": NOTE,
        }
        path = _atomic_write(_heartbeat_path(root, role, agent), payload)
    return {
        "ok": True, "action": "beat", "state": FRESH, "role": role, "agent": agent,
        "beat_id": beat_id, "tenure": tenure, "checkpoint_sha256": sha,
        "heartbeat_path": str(path), "expires_at": payload["expires_at"],
        "note": NOTE,
    }


# --------------------------------------------------------------------------- #
# Successor ranking and plan
# --------------------------------------------------------------------------- #
def _candidate_rows(root, role, record, now, *, min_units=1):
    required = record.get("required_capability") or ""
    declared = record.get("candidates", []) or []
    candidates = ({row["agent"]: row for row in declared}
                  if isinstance(declared, list) else dict(declared))
    rank_rows = resources.rank(root, capability=required, min_units=min_units,
                               now=now)
    scores = {row["agent"]: row for row in rank_rows}
    holder = record.get("holder")
    rows = []
    for name, candidate in sorted(candidates.items()):
        if name == holder:
            continue
        quota = _quota(root, name, now)
        capability_ok = bool(candidate.get("eligible"))
        quota_ok = quota["state"] == "ACTIVE" and quota["available"] >= min_units
        reason = []
        if not capability_ok:
            reason.append(f"CAPABILITY_MISMATCH:{required}")
        if quota["state"] == "UNDECLARED":
            reason.append("QUOTA_UNDECLARED")
        elif quota["state"] != "ACTIVE":
            reason.append(quota["state"])
        elif quota["available"] < min_units:
            reason.append("INSUFFICIENT_AVAILABLE")
        rows.append({
            "agent": name, "capability": candidate.get("capability"),
            "capability_ok": capability_ok, "quota_state": quota["state"],
            "available": quota["available"], "unit": quota["unit"],
            "quota_ok": quota_ok, "eligible_for_offer": capability_ok and quota_ok,
            "score": (scores.get(name) or {}).get("score", 0.0),
            "reasons": reason, "evidence": list(candidate.get("evidence", [])),
        })
    rows.sort(key=lambda row: (not row["eligible_for_offer"],
                               row["quota_state"] != "ACTIVE",
                               -float(row["score"]), row["agent"]))
    recruit = [
        {"agent": row["agent"], "provider": row["provider"],
         "available": row["available"], "unit": row["unit"], "score": row["score"]}
        for row in rank_rows
        if row["agent"] not in candidates and row["agent"] != holder and row["eligible"]
    ]
    return rows, recruit


def _plan_action(record, seat, recommended, recruit):
    state = record.get("state")
    if state == command_handoff.STANDBY:
        if recommended:
            return "CLAIM_ROLE"
        return "RECRUIT_CANDIDATE" if recruit else "NO_CANDIDATE"
    if state == command_handoff.YIELDED:
        if recommended:
            return "TAKEOVER_VACANT"
        return "RECRUIT_CANDIDATE" if recruit else "ALERT_AND_RECRUIT"
    if state == command_handoff.LIMITED:
        if seat["heartbeat"]["state"] != FRESH:
            if recommended:
                return "TAKEOVER_VACANT"
            return "RECRUIT_CANDIDATE" if recruit else "ALERT_AND_RECRUIT"
        if recommended:
            return "PREPARE_OFFER"
        return "RECRUIT_CANDIDATE" if recruit else "ALERT_AND_RECRUIT"
    if state == command_handoff.TAKING_OVER:
        return "TRANSITIONING"
    if state == command_handoff.ACTIVE:
        if seat["seat_state"] == "READY":
            return "HOLD"
        if seat["seat_state"] == "READY_UNVERIFIED_QUOTA":
            return "HOLD_QUOTA_UNVERIFIED"
        if seat["seat_state"] == "HEARTBEAT_LOST":
            return "EMERGENCY_REQUIRES_UPSTREAM"
        if seat["seat_state"] == "QUOTA_BLOCKED":
            if recommended:
                return "PREPARE_OFFER"
            return "RECRUIT_CANDIDATE" if recruit else "ALERT_AND_RECRUIT"
    return "UNKNOWN"


def plan(root, role, *, capability="", now=None):
    """Read-only succession recommendation for one role."""
    _validate_role(role)
    if capability and not resources.CAPABILITY.fullmatch(capability):
        raise ValueError("INVALID_CAPABILITY")
    moment = _now(now)
    state = _status(root)
    record = state["roles"].get(role)
    if record is None:
        raise ValueError(f"UNKNOWN_ROLE:{role}")
    seat = _seat_from_record(root, role, record, moment)
    candidates, recruit = _candidate_rows(root, role, record, moment)
    required = record.get("required_capability") or ""
    if capability and capability != required:
        candidates = []
        recruit = []
    eligible = next((row for row in candidates if row["eligible_for_offer"]), None)
    action = _plan_action(record, seat, eligible, recruit)
    recommended = (eligible if action in {"PREPARE_OFFER", "TAKEOVER_VACANT",
                                          "CLAIM_ROLE"} else None)
    blocked_by = ""
    if action == "EMERGENCY_REQUIRES_UPSTREAM":
        blocked_by = (
            "The holder's heartbeat is lost, but command_handoff.yield_role accepts "
            "only capacity signals.  A no_heartbeat/stall path must be added upstream "
            "or the holder must yield explicitly; this relay will not fabricate a "
            "time_limit signal."
        )
    return {
        "schema": SCHEMA, "action": "plan", "role": role, "at": moment.isoformat(),
        "action_required": action, "recommended": recommended,
        "candidates": candidates, "recruitable": recruit,
        "seat": seat, "blocked_by": blocked_by,
        "offer_ready": action == "PREPARE_OFFER" and recommended is not None,
        "note": NOTE,
    }


# --------------------------------------------------------------------------- #
# Delivery hands
# --------------------------------------------------------------------------- #
def _event_id(value):
    if not isinstance(value, str) or not EVENT_ID.fullmatch(value):
        raise ValueError("INVALID_WAKE_ID")
    return value


def _pwsh_path():
    """Find PowerShell 7, including its standard install when PATH is stale.

    An agent process can inherit a PATH from before PowerShell 7 was added.
    ``shutil.which`` then silently misses an installed ``pwsh.exe``; callers
    that fall back to Windows PowerShell 5.1 misread UTF-8 adapter scripts.
    The persisted user PATH is the authority here, not the environment this
    long-lived process happened to inherit.
    """
    found = shutil.which("pwsh.exe")
    if found:
        return found
    roots = (os.environ.get("ProgramFiles"), r"C:\Program Files")
    seen = set()
    for raw in roots:
        if not raw or raw in seen:
            continue
        seen.add(raw)
        candidate = Path(raw) / "PowerShell" / "7" / "pwsh.exe"
        if candidate.is_file():
            return str(candidate)
    return ""


@contextmanager
def _pwsh_in_path():
    """Expose the discovered pwsh directory only while dispatching a wake."""
    executable = _pwsh_path()
    if not executable:
        yield ""
        return
    parent = str(Path(executable).parent)
    original = os.environ.get("PATH", "")
    parts = original.split(os.pathsep)
    changed = parent not in parts
    if changed:
        os.environ["PATH"] = parent + os.pathsep + original
    try:
        yield executable
    finally:
        if changed:
            os.environ["PATH"] = original


def _deliver(root, *, target, event_id, task, sender, topic, text, entrypoint,
             bridge_root=None, dispatch=False, now=None):
    """Deposit one mailbox item and, when an adapter exists, queue a wake.

    Neither action proves delivery or acceptance.  A requested dispatch is
    attempted at most once; an unknown outcome is reported, never replayed.
    """
    target = _validate_agent(target)
    event_id = _event_id(event_id)
    moment = _now(now)
    bridge = Path(bridge_root) if bridge_root else Path(root)
    message_path = bridge / "messages" / target / f"{event_id}.json"
    mailbox_state = "PRESENT"
    if not message_path.exists():
        _atomic_write(message_path, {
            "id": event_id, "at": moment.isoformat(), "sender": sender,
            "to": target, "topic": topic, "kind": "command_relay", "text": text,
            "note": "Mailbox deposit is not delivery or ACK.",
        })
        mailbox_state = "DEPOSITED"
    adapter = (Path(__file__).resolve().parent.parent
               / "coordination" / "wake_adapters" / f"{target}.ps1")
    profile = Path(root) / "ui_profiles" / f"{target}.json"
    has_adapter = adapter.is_file() or profile.is_file()
    wake_path = bridge / "wake_queue" / f"{event_id}.json"
    wake_queued = False
    if has_adapter and not wake_path.exists():
        _atomic_write(wake_path, {
            "id": event_id, "type": "wake_request", "at": moment.isoformat(),
            "sender": sender, "to": target, "task": task,
            "reason": topic[:200], "entrypoint": str(Path(entrypoint).resolve()),
            "deliver_text": text, "no_polling": True, "dry_run": False,
            "allow_new_session": False,
            "session": {"mode": "sticky", "new_session_policy": "forbid-by-default"},
            "claim_timeout_seconds": 900,
        })
        wake_queued = True
    dispatch_state = "NOT_ATTEMPTED"
    dispatch_result = None
    dispatch_error = ""
    if dispatch:
        if not has_adapter or not wake_path.exists():
            dispatch_state = "SKIPPED_NO_ADAPTER"
        else:
            with _pwsh_in_path() as pwsh:
                if not pwsh:
                    dispatch_state = "SKIPPED_PWSH_MISSING"
                    dispatch_error = (
                        "PowerShell 7 (pwsh.exe) was not found; desktop wake was not "
                        "attempted. Install PowerShell 7 or add it to PATH. The mailbox "
                        "item and wake request are still deposited."
                    )
                else:
                    try:
                        from ..coordination import wake_ingress
                        dispatch_result = wake_ingress.dispatch_wake(bridge, event_id,
                                                                      apply=True)
                        dispatch_state = str(dispatch_result.get("state", "UNKNOWN"))
                    except Exception as exc:
                        dispatch_state = "DISPATCH_FAILED_OR_UNKNOWN"
                        dispatch_error = f"{type(exc).__name__}: {exc}"[:500]
    return {
        "mailbox_path": str(message_path), "mailbox_state": mailbox_state,
        "wake_path": str(wake_path), "wake_queued": wake_queued,
        "adapter_present": has_adapter, "dispatch_state": dispatch_state,
        "dispatch_result": dispatch_result, "dispatch_error": dispatch_error,
        "agent_accepted": False,
        "next": ("await_target_ack" if has_adapter else "needs_adapter_or_manual_relay"),
        "note": NOTE,
    }


def _safe_deliver(*args, **kwargs):
    try:
        return _deliver(*args, **kwargs)
    except Exception as exc:
        return {
            "state": "DELIVERY_FAILED", "mailbox_state": "UNKNOWN",
            "wake_queued": False, "agent_accepted": False,
            "error": f"{type(exc).__name__}: {exc}"[:500],
            "next": "inspect the mailbox/wake tree; do not assume delivery",
            "note": NOTE,
        }


def _journal(root, actor, kind, text):
    try:
        event = conversation_journal.append_event(
            Path(root), room="agent-legion", actor=actor,
            source="command-relay", kind=kind, text=text[:4000])
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)[:300]}
    return {"ok": True, "id": event.get("id", "")}


def _offer_task(role):
    return ("RELAY-" + role.upper().replace("_", "-"))[:64]


def _offer_text(offer, checkpoint):
    return (
        f"【AEP 换防邀约 {offer['offer_id']}】角色 {offer['role']} 当前持有者 "
        f"{offer['from']} 声明容量信号 {offer['signal']}。\n"
        f"原因：{offer['reason']}\n"
        f"指定接手：{offer['to']}；检查点 SHA256：{offer['checkpoint_sha256']}\n"
        f"检查点下一步：{checkpoint['next_step']}\n"
        f"完成项/产物/待办/未知：{len(checkpoint['completed'])}/"
        f"{len(checkpoint['artifacts'])}/{len(checkpoint['pending_operations'])}/"
        f"{len(checkpoint['unknowns'])}\n"
        f"接手人必须本人执行 aep coord-command-relay accept --role {offer['role']} "
        f"--agent {offer['to']} --offer-id {offer['offer_id']} "
        f"--checkpoint-sha256 {offer['checkpoint_sha256']} --evidence <你的证据>。\n"
        "收到消息、窗口聚焦或唤醒执行都不等于已接单；接受成功后必须立即 beat 才有心跳。"
    )


def _offer_view(root, row, now=None):
    offer_id = row.get("offer_id", "")
    acceptance = _read(_acceptance_path(root, offer_id)) if OFFER_ID.fullmatch(offer_id) else None
    decline = _read(_decline_path(root, offer_id)) if OFFER_ID.fullmatch(offer_id) else None
    if acceptance is not None:
        state = "ACCEPTED"
    elif decline is not None:
        state = "DECLINED"
    else:
        try:
            state = "EXPIRED" if _parse_time(row.get("expires_at")) <= _now(now) else "OFFERED"
        except (TypeError, ValueError):
            state = "INVALID"
    return {
        "offer_id": offer_id, "role": row.get("role"), "from": row.get("from"),
        "to": row.get("to"), "signal": row.get("signal"), "reason": row.get("reason"),
        "checkpoint_sha256": row.get("checkpoint_sha256"),
        "created_at": row.get("created_at"), "expires_at": row.get("expires_at"),
        "state": state, "delivery": row.get("delivery", {}),
        "accepted_handoff": (acceptance or {}).get("handoff_id", ""),
    }


def offer(root, *, actor, role, to="", signal, reason, evidence, checkpoint,
          expires_minutes=None, bridge_root=None, dispatch=False, nonce="", now=None):
    """Holder-only.  Persist a fenced offer; never changes role state."""
    actor = _validate_agent(actor)
    _validate_role(role)
    if to:
        to = _validate_agent(to)
    if signal not in CAPACITY_SIGNALS:
        raise ValueError(f"INVALID_SIGNAL:{signal}")
    reason = _text(reason, "reason")
    proof = _evidence(evidence)
    expires = _minutes(expires_minutes, "expires_minutes", DEFAULT_OFFER_MINUTES,
                       MAX_OFFER_MINUTES)
    if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
        raise ValueError("invalid nonce")
    moment = _now(now)
    with _role_lock(root, role):
        state = _status(root)
        record = state["roles"].get(role)
        if record is None:
            raise ValueError(f"UNKNOWN_ROLE:{role}")
        if record.get("holder") != actor:
            raise ValueError("NOT_HOLDER")
        if record.get("state") not in {command_handoff.ACTIVE, command_handoff.LIMITED}:
            raise ValueError(f"ROLE_NOT_OFFERABLE:{record.get('state')}")
        seat = _seat_from_record(root, role, record, moment)
        if seat["heartbeat"]["state"] != FRESH:
            raise ValueError(f"HOLDER_HEARTBEAT_NOT_FRESH:{seat['heartbeat']['state']}")
        candidates, _ = _candidate_rows(root, role, record, moment)
        if not to:
            chosen = next((row for row in candidates if row["eligible_for_offer"]), None)
            if chosen is None:
                raise ValueError("NO_ELIGIBLE_CANDIDATE")
            to = chosen["agent"]
        target = next((row for row in candidates if row["agent"] == to), None)
        if target is None:
            raise ValueError("TARGET_NOT_CANDIDATE")
        if not target["capability_ok"]:
            raise ValueError(f"TARGET_CAPABILITY_MISMATCH:{record.get('required_capability')}")
        if not target["quota_ok"]:
            raise ValueError("TARGET_QUOTA_NOT_ELIGIBLE:" + ",".join(target["reasons"]))
        quota = _quota(root, actor, moment)
        sha, pack, normalized_checkpoint = _store_checkpoint(
            root, checkpoint, role=role, agent=actor, quota=quota, now=moment)
        material = {
            "role": role, "from": actor, "to": to, "signal": signal,
            "reason": reason, "evidence": proof, "checkpoint_sha256": sha,
            "expires_minutes": expires, "nonce": nonce,
        }
        offer_id = "R" + _digest(material)[:19]
        path = _offer_path(root, offer_id)
        existing = _read(path) if path.is_file() else None
        if existing is not None:
            if (existing.get("role") != role or existing.get("from") != actor
                    or existing.get("to") != to or existing.get("signal") != signal
                    or existing.get("reason") != reason
                    or existing.get("checkpoint_sha256") != sha
                    or list(existing.get("evidence", [])) != proof):
                raise ValueError("OFFER_ID_COLLISION")
            view = _offer_view(root, existing, now=moment)
            view.update({"action": "offer", "idempotent": True,
                         "no_redelivery": True, "note": NOTE})
            return view
        row = {
            "schema": OFFER_SCHEMA, "offer_id": offer_id, "role": role,
            "from": actor, "to": to, "signal": signal, "reason": reason,
            "evidence": proof, "checkpoint_sha256": sha,
            "checkpoint_path": str(pack),
            "created_at": moment.isoformat(),
            "expires_at": (moment + timedelta(minutes=expires)).isoformat(),
            "nonce": nonce, "delivery": {}, "note": NOTE,
        }
        _atomic_write(path, row)
        text = _offer_text(row, normalized_checkpoint)
        delivery = _safe_deliver(
            root, target=to, event_id=offer_id, task=_offer_task(role),
            sender=actor, topic=f"command handoff offer {role}", text=text,
            entrypoint=path, bridge_root=bridge_root, dispatch=dispatch,
            now=moment)
        row["delivery"] = delivery
        _atomic_write(path, row)
    journal = _journal(root, actor, "handoff_offer", text)
    view = _offer_view(root, row, now=moment)
    view.update({"schema": SCHEMA, "action": "offer", "idempotent": False,
                 "checkpoint_path": str(pack), "journal": journal,
                 "note": NOTE})
    return view


def _find_offer_handoff(record, agent, offer_id):
    token = f"relay-accept:{offer_id}"
    for handoff in reversed(record.get("handoffs", [])):
        if handoff.get("to") != agent:
            continue
        if token in [str(item) for item in handoff.get("evidence", [])]:
            return handoff
    return None


def accept(root, *, agent, role, offer_id, checkpoint_sha256, evidence,
           bridge_root=None, dispatch=False, now=None):
    """Target-only acceptance.  Verifies the pack, yields and takes over."""
    agent = _validate_agent(agent)
    _validate_role(role)
    offer_id = _validate_offer_id(offer_id)
    sha = _validate_sha(checkpoint_sha256)
    proof = _evidence(evidence)
    moment = _now(now)
    acceptance_path = _acceptance_path(root, offer_id)
    existing_acceptance = _read(acceptance_path) if acceptance_path.is_file() else None
    if existing_acceptance is not None:
        if (existing_acceptance.get("agent") != agent
                or existing_acceptance.get("role") != role
                or existing_acceptance.get("checkpoint_sha256") != sha):
            raise ValueError("ACCEPTANCE_CONFLICT")
        current = _status(root)["roles"].get(role, {})
        return {**existing_acceptance, "schema": SCHEMA, "action": "accept",
                "idempotent": True, "no_redelivery": True,
                "current_holder": current.get("holder"),
                "still_holder": (current.get("holder") == agent
                                 and current.get("state") == command_handoff.ACTIVE),
                "note": NOTE}
    offer_path = _offer_path(root, offer_id)
    row = _read(offer_path) if offer_path.is_file() else None
    if row is None:
        raise ValueError("OFFER_MISSING")
    if row.get("schema") != OFFER_SCHEMA:
        raise ValueError("OFFER_INVALID")
    if row.get("role") != role:
        raise ValueError("OFFER_ROLE_MISMATCH")
    if row.get("to") != agent:
        raise ValueError("NOT_OFFER_TARGET")
    if row.get("checkpoint_sha256") != sha:
        raise ValueError("CHECKPOINT_MISMATCH")
    if _read(_decline_path(root, offer_id)) is not None:
        raise ValueError("OFFER_DECLINED")
    try:
        if _parse_time(row.get("expires_at")) < moment:
            raise ValueError("OFFER_EXPIRED")
    except (TypeError, ValueError) as exc:
        if str(exc) == "OFFER_EXPIRED":
            raise
        raise ValueError("OFFER_EXPIRY_INVALID") from exc
    _verify_pack(root, sha)
    quota_gate = resources.gate(root, agent=agent, units=1, allow_unknown=False,
                                now=moment)
    if not quota_gate["allowed"]:
        raise ValueError("TARGET_QUOTA_GATE:" + ",".join(quota_gate["reasons"]))
    accept_evidence = [f"relay-accept:{offer_id}", f"checkpoint:{sha}", *proof]
    with _role_lock(root, role):
        moment = _now(now)
        if _read(_decline_path(root, offer_id)) is not None:
            raise ValueError("OFFER_DECLINED")
        try:
            if _parse_time(row.get("expires_at")) < moment:
                raise ValueError("OFFER_EXPIRED")
        except (TypeError, ValueError) as exc:
            if str(exc) == "OFFER_EXPIRED":
                raise
            raise ValueError("OFFER_EXPIRY_INVALID") from exc
        state = _status(root)
        record = state["roles"].get(role)
        if record is None:
            raise ValueError(f"UNKNOWN_ROLE:{role}")
        if record.get("holder") == agent and record.get("state") == command_handoff.ACTIVE:
            handoff = _find_offer_handoff(record, agent, offer_id)
            if handoff is None:
                raise ValueError("ALREADY_HOLDER_BY_ANOTHER_PATH")
            row = _read(offer_path) or row
            acceptance = {
                "schema": ACCEPT_SCHEMA, "state": "ACCEPTED", "offer_id": offer_id,
                "role": role, "from": row.get("from"), "agent": agent,
                "checkpoint_sha256": sha, "evidence": proof,
                "handoff_id": handoff.get("handoff_id", ""),
                "event_id": handoff.get("event_id", ""),
                "transition": handoff.get("result", {}).get("transition", []),
                "accepted_at": moment.isoformat(),
                "heartbeat_required": True,
                "delivery": {"state": "UNVERIFIED_NO_REPLAY"},
                "note": NOTE,
            }
            _atomic_write(acceptance_path, acceptance)
            return {**acceptance, "schema": SCHEMA, "action": "accept",
                    "idempotent": True, "recovered": True, "no_redelivery": True,
                    "note": NOTE}
        if record.get("holder") != row.get("from"):
            raise ValueError("ROLE_CHANGED")
        if record.get("state") not in command_handoff.TAKEOVER_STATES:
            command_handoff.yield_role(
                root, from_agent=row["from"], role=role, signal=row["signal"],
                evidence=[*row.get("evidence", []), f"offer:{offer_id}",
                          f"checkpoint:{sha}", *proof])
        result = command_handoff.takeover(
            root, to_agent=agent, role=role, evidence=accept_evidence)
        new_state = _status(root)
        new_record = new_state["roles"][role]
        new_tenure = _tenure(new_record)
        acceptance = {
            "schema": ACCEPT_SCHEMA, "state": "ACCEPTED", "offer_id": offer_id,
            "role": role, "from": row.get("from"), "agent": agent,
            "checkpoint_sha256": sha, "evidence": proof,
            "handoff_id": result.get("handoff_id", ""),
            "event_id": result.get("event_id", ""),
            "transition": result.get("transition", []),
            "accepted_at": moment.isoformat(),
            "heartbeat_required": True, "tenure": new_tenure,
            "delivery": {}, "note": NOTE,
        }
        _atomic_write(acceptance_path, acceptance)
        row = _read(offer_path) or row
    notice = (
        f"【AEP 换防已接受 {offer_id}】角色 {role} 已由 {agent} 接手，"
        f"handoff {acceptance['handoff_id']}，检查点 {sha}。\n"
        "请新持有者立即 beat；接收通知本身不是心跳，也不是对任何任务的 ACK。"
    )
    delivery = _safe_deliver(
        root, target=row["from"], event_id=offer_id + "-A",
        task=_offer_task(role), sender=agent,
        topic=f"command handoff accepted {role}", text=notice,
        entrypoint=acceptance_path, bridge_root=bridge_root,
        dispatch=dispatch, now=moment)
    acceptance["delivery"] = delivery
    _atomic_write(acceptance_path, acceptance)
    journal = _journal(root, agent, "handoff_accept", notice)
    heartbeat = heartbeat_status(root, role, agent, now=moment, tenure=acceptance["tenure"])
    return {**acceptance, "schema": SCHEMA, "action": "accept",
            "idempotent": False, "heartbeat": heartbeat,
            "journal": journal, "no_redelivery": False, "note": NOTE}


def decline(root, *, agent, role, offer_id, reason, bridge_root=None, now=None):
    """Target rejects an offer; role state is unchanged."""
    agent = _validate_agent(agent)
    _validate_role(role)
    offer_id = _validate_offer_id(offer_id)
    reason = _text(reason, "reason")
    moment = _now(now)
    path = _decline_path(root, offer_id)
    with _role_lock(root, role):
        offer_row = _read(_offer_path(root, offer_id))
        if offer_row is None:
            raise ValueError("OFFER_MISSING")
        if offer_row.get("role") != role:
            raise ValueError("OFFER_ROLE_MISMATCH")
        if offer_row.get("to") != agent:
            raise ValueError("NOT_OFFER_TARGET")
        if _read(_acceptance_path(root, offer_id)) is not None:
            raise ValueError("ALREADY_ACCEPTED")
        existing = _read(path) if path.is_file() else None
        if existing is not None:
            if existing.get("agent") != agent:
                raise ValueError("ALREADY_DECLINED")
            return {**existing, "schema": SCHEMA, "action": "decline",
                    "idempotent": True, "no_redelivery": True, "note": NOTE}
        row = {
            "schema": DECLINE_SCHEMA, "offer_id": offer_id, "role": role,
            "agent": agent, "reason": reason, "declined_at": moment.isoformat(),
            "note": NOTE,
        }
        _atomic_write(path, row)
    notice = (f"【AEP 换防邀约被拒 {offer_id}】{agent} 拒绝接手角色 {role}。"
              f"原因：{reason}。角色状态未改变，请另寻候选或继续持有。")
    delivery = _safe_deliver(
        root, target=offer_row["from"], event_id=offer_id + "-D",
        task=_offer_task(role), sender=agent,
        topic=f"command handoff declined {role}", text=notice,
        entrypoint=path, bridge_root=bridge_root, now=moment)
    journal = _journal(root, agent, "handoff_decline", notice)
    return {**row, "action": "decline", "idempotent": False,
            "delivery": delivery, "journal": journal,
            "no_redelivery": False, "note": NOTE}


def takeover_vacant(root, *, agent, role, evidence, checkpoint=None,
                    bridge_root=None, dispatch=False, now=None):
    """Take a YIELDED/LIMITED seat directly when the old holder is not live."""
    agent = _validate_agent(agent)
    _validate_role(role)
    proof = _evidence(evidence)
    moment = _now(now)
    with _role_lock(root, role):
        state = _status(root)
        record = state["roles"].get(role)
        if record is None:
            raise ValueError(f"UNKNOWN_ROLE:{role}")
        if record.get("holder") == agent and record.get("state") == command_handoff.ACTIVE:
            return {"schema": SCHEMA, "action": "takeover", "role": role,
                    "agent": agent, "state": command_handoff.ACTIVE,
                    "already_holder": True, "heartbeat_required": True,
                    "note": NOTE}
        if record.get("state") not in command_handoff.TAKEOVER_STATES:
            raise ValueError(f"ROLE_NOT_TAKEOVERABLE:{record.get('state')}")
        if record.get("state") == command_handoff.LIMITED:
            seat = _seat_from_record(root, role, record, moment)
            if seat["heartbeat"]["state"] == FRESH and seat["quota"]["state"] == "ACTIVE":
                raise ValueError("HOLDER_STILL_LIVE_USE_OFFER")
        candidates, _ = _candidate_rows(root, role, record, moment)
        target = next((row for row in candidates if row["agent"] == agent), None)
        if target is None:
            raise ValueError("NOT_A_CANDIDATE")
        if not target["capability_ok"]:
            raise ValueError(f"CAPABILITY_MISMATCH:{record.get('required_capability')}")
        gate = resources.gate(root, agent=agent, units=1, allow_unknown=False, now=moment)
        if not gate["allowed"]:
            raise ValueError("TARGET_QUOTA_GATE:" + ",".join(gate["reasons"]))
        extra = []
        checkpoint_path = ""
        if checkpoint is not None:
            quota = _quota(root, agent, moment)
            sha, pack, _ = _store_checkpoint(root, checkpoint, role=role, agent=agent,
                                             quota=quota, now=moment)
            extra = [f"checkpoint:{sha}"]
            checkpoint_path = str(pack)
        result = command_handoff.takeover(
            root, to_agent=agent, role=role,
            evidence=[f"relay-takeover:{role}", *extra, *proof])
        new_state = _status(root)
        new_record = new_state["roles"][role]
        new_tenure = _tenure(new_record)
        receipt = {
            "schema": TAKEOVER_SCHEMA, "state": "ACCEPTED", "role": role,
            "agent": agent, "from": result.get("from"),
            "handoff_id": result.get("handoff_id", ""),
            "event_id": result.get("event_id", ""),
            "transition": result.get("transition", []),
            "checkpoint_path": checkpoint_path,
            "accepted_at": moment.isoformat(), "heartbeat_required": True,
            "tenure": new_tenure, "note": NOTE,
        }
        receipt_path = Path(root) / "relay" / "takeovers" / f"{receipt['handoff_id']}.json"
        _atomic_write(receipt_path, receipt)
    old_holder = receipt.get("from")
    delivery = {"state": "NOT_ATTEMPTED"}
    if old_holder:
        notice = (
            f"【AEP 角色接管 {role}】{agent} 已接手 YIELDED/LIMITED 席位，"
            f"handoff {receipt['handoff_id']}。旧持有者 {old_holder} 请停止派工；"
            "如仍可用请报告状态。接管成功不是心跳，新持有者必须立即 beat。"
        )
        delivery = _safe_deliver(
            root, target=old_holder, event_id=receipt["handoff_id"] + "-N",
            task=_offer_task(role), sender=agent,
            topic=f"command handoff takeover {role}", text=notice,
            entrypoint=receipt_path, bridge_root=bridge_root,
            dispatch=dispatch, now=moment)
        journal = _journal(root, agent, "handoff_takeover", notice)
    else:
        journal = {"ok": True, "id": ""}
    heartbeat = heartbeat_status(root, role, agent, now=moment,
                                 tenure=receipt["tenure"])
    return {**receipt, "schema": SCHEMA, "action": "takeover",
            "receipt_path": str(receipt_path), "heartbeat": heartbeat,
            "delivery": delivery, "journal": journal, "note": NOTE}


def summary(root, *, now=None):
    """Read-only overview of seats, offers, quota envelopes and chain state."""
    moment = _now(now)
    state = _status(root)
    seats = {}
    for role, record in sorted(state["roles"].items()):
        seats[role] = _seat_from_record(root, role, record, moment)
    folder = Path(root) / "relay" / "offers"
    offers, invalid = [], []
    paths = sorted(folder.glob("*.json")) if folder.is_dir() else []
    for path in paths:
        row = _read(path)
        if row is None or row.get("schema") != OFFER_SCHEMA:
            invalid.append(str(path))
            continue
        view = _offer_view(root, row, now=moment)
        try:
            view["_sort"] = _parse_time(view.get("created_at")).timestamp()
        except (TypeError, ValueError):
            view["_sort"] = 0.0
        offers.append(view)
    offers.sort(key=lambda row: (row["_sort"], row["offer_id"]), reverse=True)
    for view in offers:
        view.pop("_sort", None)
    return {
        "schema": SUMMARY_SCHEMA, "at": moment.isoformat(),
        "chain_valid": state.get("chain_valid"),
        "seats": seats,
        "offers": offers[:100], "offer_total": len(paths),
        "invalid_offers": invalid[:20],
        "resources": resources.snapshot(root, at=moment),
        "note": NOTE,
    }