"""Read-only recovery evidence for routing; never acquire or revoke ownership."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path

from .routing import contains, normalized


def timestamp(value) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def read_object(path: Path, warnings: list[str]) -> dict:
    if not path.exists():
        return {}
    try:
        if path.stat().st_size > 1048576:
            raise ValueError("exceeds 1 MiB")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("object required")
        return data
    except (OSError, ValueError) as exc:
        warnings.append(f"unreadable state {path.name}: {exc}")
        return {}


def read_events(root: Path, warnings: list[str]) -> list[dict]:
    path = Path(root) / "events.jsonl"
    if not path.exists():
        return []
    events = []
    try:
        with path.open(encoding="utf-8-sig") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError("object required")
                    events.append(event)
                except ValueError:
                    if len(warnings) < 20:
                        warnings.append(f"invalid kernel event line {number}; state may be incomplete")
    except OSError as exc:
        warnings.append(f"kernel ledger unavailable: {exc}")
    return events


def clipped(value, depth: int = 0):
    if depth > 3:
        return "[summary depth limit]"
    if isinstance(value, str):
        return value[:600] + ("…" if len(value) > 600 else "")
    if isinstance(value, list):
        return [clipped(item, depth + 1) for item in value[:8]]
    if isinstance(value, dict):
        return {str(k)[:80]: clipped(v, depth + 1) for k, v in list(value.items())[:12]}
    return value


def select_resource_type(intent: str) -> dict:
    text = normalized(intent)
    if any(contains(text, term) for term in ("上传", "upload", "提交", "submit")):
        return {"kind": "upload", "lease_type": "UPLOAD_INTENT_LEASE", "additional_lease_types": ["BROWSER_SESSION_LEASE"], "required_fields": ["resource", "task_id", "artifact_sha"], "note": "Lock the upload intent and the browser session when using a browser."}
    if any(contains(text, term) for term in ("支付", "payment", "pay")):
        return {"kind": "payment", "lease_type": "TASK_WRITE_LEASE", "additional_lease_types": [], "required_fields": ["resource"], "note": "Generic serialization only; no payment executor or payment authorization is supplied."}
    if any(contains(text, term) for term in ("浏览器", "browser")):
        return {"kind": "browser", "lease_type": "BROWSER_SESSION_LEASE", "additional_lease_types": [], "required_fields": ["resource"], "note": "Use the same canonical browser resource across agents."}
    return {"kind": "task-write", "lease_type": "TASK_WRITE_LEASE", "additional_lease_types": [], "required_fields": ["resource"], "note": "Use a shared canonical resource key; reserve and reconcile external operations separately."}


def analyze_route_state(kernel_root: Path, coord_root: Path, task: str = "", agent: str = "", *, now: datetime | None = None, stale_after_seconds: float = 43200) -> dict:
    now = now or datetime.now(timezone.utc)
    warnings: list[str] = []
    events = read_events(kernel_root, warnings)
    leases, operations, checkpoints = {}, {}, []
    for event in events:
        kind = event.get("kind", "")
        if isinstance(kind, str) and kind.startswith("LEASE_"):
            leases[(str(event.get("lease_type")), str(event.get("resource")))] = event
        elif kind == "OPERATION":
            operations[str(event.get("operation_key"))] = event
        elif kind == "CHECKPOINT_WRITTEN" and (not task or event.get("task_id") == task):
            checkpoints.append(event)
    active, expired = [], []
    for event in leases.values():
        if event.get("kind") not in {"LEASE_ACQUIRED", "LEASE_RENEWED"}:
            continue
        expiry = timestamp(event.get("expires_at"))
        if expiry is None:
            warnings.append("lease expiry missing or invalid; treat ownership as unresolved")
            active.append(event)
        elif expiry <= now:
            expired.append(event)
        else:
            active.append(event)
    def relevant(event):
        request = event.get("request")
        request = request if isinstance(request, dict) else {}
        associated = event.get("task_id") or request.get("task_id") or request.get("task")
        return not task or not associated or associated == task
    related = [event for event in operations.values() if relevant(event)]
    unknown = [event for event in related if event.get("state") == "UNKNOWN"]
    inflight = [event for event in related if event.get("state") in {"RESERVED", "STARTED"}]
    summaries = [{"task_id": event.get("task_id"), "executor_id": event.get("executor_id"), "at": event.get("at"), "checkpoint": clipped(event.get("checkpoint", {})), "checkpoint_hash": event.get("checkpoint_hash"), "source": str(Path(kernel_root) / "events.jsonl")} for event in checkpoints[-3:]]
    claim_dir = Path(coord_root) / "claims" / task if task else None
    owner = read_object(claim_dir / "owner.json", warnings) if claim_dir else {}
    state = read_object(Path(coord_root) / "task_states" / f"{task}.json", warnings) if task else {}
    owner_id = owner.get("agent")
    if state and owner_id and state.get("owner") != owner_id:
        warnings.append("task-state owner does not match current claim; do not infer takeover")
        state = {}
    at = timestamp(state.get("at") or owner.get("at"))
    age = max(0, (now - at).total_seconds()) if at else None
    if at and at > now:
        warnings.append("task timestamp is in the future; freshness is uncertain")
    try:
        ttl = float(state.get("stale_after_hours", stale_after_seconds / 3600)) * 3600
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("invalid ttl")
    except (TypeError, ValueError):
        ttl = stale_after_seconds
        warnings.append("invalid stale threshold; using default")
    phase = str(state.get("phase", "")).lower()
    status = "unclaimed" if not claim_dir or not claim_dir.exists() else "unknown"
    if phase in {"waiting", "waiting_user", "waiting_external"} or state.get("human_required"):
        status = "waiting"
    elif phase in {"done", "abandoned", "succeeded", "cancelled"}:
        status = "terminal"
    elif age is not None:
        status = "suspected-stale" if age > ttl else "active"
    if claim_dir and claim_dir.exists() and not owner:
        status = "claim-pending-or-unreadable"
    recovery = {"status": status, "owner": owner_id, "phase": phase, "age_seconds": age, "stale_after_seconds": ttl, "takeover_candidate": bool(owner_id) and status == "suspected-stale" and not active and not unknown and not inflight and not warnings, "automatic_takeover_allowed": False, "next_action": clipped(state.get("next_action", "")), "reason": "Age is evidence for inspection, not permission to steal a claim. Waiting requires its expected event."}
    return {"active_leases": active, "expired_leases": expired, "unknown_operations": unknown, "inflight_operations": inflight, "latest_checkpoints": summaries, "recovery": recovery, "warnings": warnings, "owner": owner, "task_state": clipped(state), "events": events}


def continuation_from_events(events: list[dict], agent: str, workspace: str, session: dict) -> dict:
    resolved = str(Path(workspace).resolve()).casefold()
    binding = next((event for event in reversed(events) if event.get("kind") == "SESSION_BOUND" and event.get("agent") == agent and str(event.get("workspace", "")).casefold() == resolved), None)
    if binding:
        resumable = bool(binding.get("resume_supported")) and bool(binding.get("session_id"))
        return {"action": "RESUME_EXISTING" if resumable else "BLOCKED_HANDOFF_REQUIRED", "session_id": binding.get("session_id"), "provider": binding.get("provider"), "source": "kernel-binding", "new_session_allowed": False}
    if session.get("thread_id"):
        return {"action": "VERIFY_ADAPTER_RESUME", "session_id": session["thread_id"], "source": "coord-session", "new_session_allowed": False}
    return {"action": "USE_CURRENT_SESSION", "session_id": None, "source": "current-agent", "new_session_allowed": False, "reason": "Continue this chat; binding is needed only to resume it from another agent."}
