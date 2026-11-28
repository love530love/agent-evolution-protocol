"""One explicit, evidence-graded acceptance envelope for a desktop delivery.

The desktop transport already knows how to do the difficult and dangerous
part: bind one original session, type once, prove a visible message, and wait
for the target's challenge response.  Historically those facts were scattered
between a profile relay receipt, a commander's short status, and an optional
readback.  That made it easy to call a focused window, a queued draft, or a
mailbox deposit a completed delivery.

This module does not create another transport.  It wraps the existing
``ui_fallback.run`` result in one manifest with the only five evidence stages
that matter:

``SESSION_LOCATED -> INPUT_SUBMITTED -> MESSAGE_VISIBLE -> AGENT_ACK
-> TASK_RESULT_READBACK``.

Nothing here types, clicks, launches a client, or sends a message unless the
caller explicitly invokes :func:`execute` with ``apply=True``.  A normal
preview validates the profile and hashes the proposed request only.  The
manifest deliberately holds on missing evidence and never retries an
uncertain send.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

from . import result_readback, ui_fallback

SCHEMA = "aep-desktop-original-session-acceptance-v1"
STAGES = (
    "SESSION_LOCATED",
    "INPUT_SUBMITTED",
    "MESSAGE_VISIBLE",
    "AGENT_ACK",
    "TASK_RESULT_READBACK",
)

# These states occur before a relay has established the intended original
# conversation.  They are listed rather than guessed from a process/window
# name so that a new state holds by default.
_BEFORE_SESSION = frozenset({
    "NO_WINDOW", "LAUNCH_UNVERIFIED", "SESSION_BINDING_REQUIRED",
    "SESSION_NOT_VERIFIED", "SESSION_ATTESTATION_REQUIRED",
    "SESSION_ATTESTATION_FAILED", "MODEL_NOT_VERIFIED", "SESSION_BUSY",
    "SESSION_UNCERTAIN", "WINDOW_OR_INPUT_AMBIGUOUS", "NO_SAFE_COMPOSER",
    "EXECUTOR_TOO_LOW",
})
_UNCERTAIN = frozenset({
    "SEND_UNCERTAIN", "URGENT_INTERJECT_UNCERTAIN", "SESSION_UNCERTAIN",
    "SESSION_WINDOW_CHANGED", "SESSION_CHANGED", "MODEL_CHANGED",
})


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _require_message(message: str) -> str:
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message must be a nonempty string")
    return message


def _require_event(event_id: str) -> str:
    if not isinstance(event_id, str) or not ui_fallback.EVENT.fullmatch(event_id):
        raise ValueError("event_id must match the desktop relay event contract")
    return event_id


def _stage(verified: bool, *, reason: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "verified": bool(verified),
        "state": "VERIFIED" if verified else "NOT_PROVEN",
        "reason": reason,
        "evidence": dict(evidence or {}),
    }


def _profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent": profile["agent"],
        "session_marker": profile["session_marker"],
        "session_provider": profile.get("session_provider") or "uia",
        "strict_session_attestation": bool(profile.get("require_strict_session_attestation")),
        "profile_sha256": _digest(profile),
    }


def _session_stage(profile: dict[str, Any], relay: dict[str, Any]) -> dict[str, Any]:
    state = str(relay.get("state") or "")
    if state in _BEFORE_SESSION:
        return _stage(False, reason=state or "SESSION_NOT_PROVEN")
    strict = bool(profile.get("require_strict_session_attestation"))
    attestation = relay.get("session_attestation")
    if strict:
        valid = isinstance(attestation, dict) and attestation.get("valid") is True
        return _stage(valid,
                      reason="STRICT_ATTESTATION_VALID" if valid else "STRICT_ATTESTATION_MISSING_OR_INVALID",
                      evidence={key: attestation.get(key) for key in
                                ("reason", "attestation_path", "evidence_path", "surface")}
                               if isinstance(attestation, dict) else {})
    marker = str(relay.get("session_marker") or "")
    valid = bool(marker and marker == profile["session_marker"])
    return _stage(valid,
                  reason="PROFILED_MARKER_BOUND" if valid else "PROFILED_MARKER_NOT_RECORDED",
                  evidence={"session_marker": marker})


def _readback_stage(*, profile: dict[str, Any], relay: dict[str, Any], message: str,
                    event_id: str, result_marker: str, acknowledged: bool,
                    readback_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not acknowledged:
        return _stage(False, reason="AGENT_ACK_REQUIRED")
    if not result_marker:
        return _stage(False, reason="RESULT_MARKER_REQUIRED")
    if readback_events is None:
        source = "ACK_REPLY"
        events = [{
            "id": f"relay:{event_id}", "role": "assistant", "direction": "incoming",
            "session_id": profile["session_marker"],
            "added_lines": [str(relay.get("reply_excerpt") or "")],
        }]
    else:
        source = "TRANSCRIPT_BUS"
        if not isinstance(readback_events, list) or any(not isinstance(item, dict)
                                                        for item in readback_events):
            raise ValueError("readback_events must be a list of transcript events")
        events = list(readback_events)
    session = result_readback.observe(
        events, session_id=profile["session_marker"], own_text=message)
    attributable = set(session["attributable"])
    bound_events = [item for item in events if str(item.get("id") or "") in attributable]
    observed = result_readback.marker_evidence(bound_events, result_marker, own_text=message)
    evidence = {
        "source": source,
        "session": session,
        "marker": observed,
        "readback_events_sha256": _digest(events),
    }
    if not session["session_checked"] or session["session_unknown"]:
        return _stage(False, reason="RESULT_READBACK_SESSION_NOT_VERIFIED", evidence=evidence)
    return _stage(observed["seen"],
                  reason="RESULT_MARKER_SEEN" if observed["seen"] else
                         "RESULT_MARKER_NOT_VISIBLE_IN_READBACK",
                  evidence=evidence)


def _verdict(stages: dict[str, dict[str, Any]], relay: dict[str, Any] | None) -> dict[str, str]:
    if relay is None:
        return {"state": "PREVIEW", "next_required": "SESSION_LOCATED"}
    relay_state = str(relay.get("state") or "")
    if relay_state in _UNCERTAIN:
        return {"state": "UNCERTAIN", "next_required": "RECONCILE_ORIGINAL_SESSION"}
    for stage in STAGES:
        if not stages[stage]["verified"]:
            if stage == "AGENT_ACK" and stages["MESSAGE_VISIBLE"]["verified"]:
                return {"state": "AWAITING_ACK", "next_required": stage}
            if stage == "TASK_RESULT_READBACK" and stages["AGENT_ACK"]["verified"]:
                return {"state": "AWAITING_TASK_RESULT", "next_required": stage}
            return {"state": "HOLD", "next_required": stage}
    return {"state": "COMPLETE", "next_required": "NONE"}


def build_manifest(*, profile: dict[str, Any], message: str, event_id: str,
                   relay: dict[str, Any] | None = None,
                   result_marker: str = "", mode: str = "PREVIEW",
                   receipt_integrity: dict[str, Any] | None = None,
                   readback_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a serializable five-stage manifest from one relay result.

    ``relay`` is intentionally an input, so tests and downstream agents can
    recompute a verdict without importing a GUI provider.  No filesystem or UI
    action occurs in this function.
    """
    message = _require_message(message)
    event_id = _require_event(event_id)
    profile = ui_fallback.normalise_profile(dict(profile))
    if relay is not None and not isinstance(relay, dict):
        raise ValueError("relay must be an object when provided")
    summary = _profile_summary(profile)
    if relay is None:
        stages = {stage: _stage(False, reason="APPLY_REQUIRED") for stage in STAGES}
        relay_summary: dict[str, Any] = {"state": "NOT_RUN"}
    else:
        relay_identity = {
            "verified": (relay.get("agent") == profile["agent"] and
                         relay.get("event_id") == event_id),
            "expected_agent": profile["agent"],
            "actual_agent": str(relay.get("agent") or ""),
            "expected_event_id": event_id,
            "actual_event_id": str(relay.get("event_id") or ""),
        }
        integrity = dict(receipt_integrity or {
            "verified": False,
            "reason": "PERSISTED_RECEIPT_REQUIRED",
        })
        session = (_session_stage(profile, relay) if relay_identity["verified"] else
                   _stage(False, reason="RELAY_IDENTITY_MISMATCH", evidence=relay_identity))
        uncertain = str(relay.get("state") or "") in _UNCERTAIN
        # A later stage can be green only if every earlier stage was proven
        # against this exact target and event. Raw relay flags remain in the
        # receipt for diagnosis, but cannot bypass this acceptance chain.
        delivered = (session["verified"] and not uncertain and
                     relay.get("task_delivered") is True)
        accepted = delivered and relay.get("agent_accepted") is True
        stages = {
            "SESSION_LOCATED": session,
            # A verified visible message necessarily crossed the client input
            # boundary. Conversely a mere adapter "submitted" report is not
            # enough to promote this stage.
            "INPUT_SUBMITTED": _stage(
                delivered,
                reason=("MESSAGE_VISIBLE_IMPLIES_SUBMITTED" if delivered else
                        "RELAY_IDENTITY_MISMATCH" if not relay_identity["verified"] else
                        "ORIGINAL_SESSION_NOT_VERIFIED" if not session["verified"] else
                        "SEND_OUTCOME_UNCERTAIN" if uncertain else "INPUT_NOT_VERIFIED")),
            "MESSAGE_VISIBLE": _stage(
                delivered,
                reason=("RELAY_MESSAGE_VISIBLE" if delivered else
                        "RELAY_IDENTITY_MISMATCH" if not relay_identity["verified"] else
                        "ORIGINAL_SESSION_NOT_VERIFIED" if not session["verified"] else
                        "SEND_OUTCOME_UNCERTAIN" if uncertain else
                        "MESSAGE_VISIBLE_NOT_PROVEN")),
            "AGENT_ACK": _stage(
                accepted,
                reason=("TARGET_CHALLENGE_REPLY_VERIFIED" if accepted else
                        "MESSAGE_VISIBLE_REQUIRED" if not delivered else
                        "TARGET_ACK_NOT_PROVEN"),
                evidence={"ack_evidence": relay.get("ack_evidence", "")}),
            "TASK_RESULT_READBACK": _readback_stage(
                profile=profile, relay=relay, message=message, result_marker=result_marker,
                event_id=event_id, acknowledged=accepted, readback_events=readback_events),
        }
        relay_summary = {
            "state": str(relay.get("state") or "UNKNOWN"),
            "task_delivered": relay.get("task_delivered") is True,
            "agent_accepted": relay.get("agent_accepted") is True,
            "receipt_file": str(relay.get("receipt_file") or ""),
            "relay_sha256": _digest(relay),
            "identity": relay_identity,
            "receipt_integrity": integrity,
        }
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "at": _utc(),
        "mode": mode,
        "event_id": event_id,
        "target": summary,
        "request": {
            "message_sha256": sha256(message.encode("utf-8")).hexdigest(),
            "message_characters": len(message),
            "result_marker": result_marker,
        },
        "stages": stages,
        "relay": relay_summary,
        "note": ("A completed manifest proves only these five named stages. "
                 "It does not authorise a retry, a new session, or a different transport."),
    }
    manifest["verdict"] = _verdict(stages, relay)
    if relay is not None and not relay_summary["identity"]["verified"]:
        manifest["verdict"] = {"state": "HOLD", "next_required": "RELAY_IDENTITY"}
    elif relay is not None and not relay_summary["receipt_integrity"].get("verified"):
        manifest["verdict"] = {"state": "HOLD", "next_required": "PERSISTED_RELAY_RECEIPT"}
    manifest["manifest_sha256"] = _digest(manifest)
    return manifest


def preview(*, profile: dict[str, Any], message: str, event_id: str,
            result_marker: str = "") -> dict[str, Any]:
    """Validate and describe one intended run without writing or touching UI."""
    return build_manifest(profile=profile, message=message, event_id=event_id,
                          result_marker=result_marker, mode="PREVIEW")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _persisted_relay(*, profile: dict[str, Any], event_id: str,
                     receipt_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read the durable relay receipt back before advancing acceptance.

    The in-memory return is a convenience to the caller, not durable evidence
    for a later commander. A missing, unreadable, or mis-bound file remains a
    visible HOLD rather than becoming a successful manifest.
    """
    path = Path(receipt_path)
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ({"agent": profile["agent"], "event_id": event_id,
                 "state": "RECEIPT_PERSISTENCE_FAILED", "task_delivered": False,
                 "agent_accepted": False, "receipt_file": str(path)},
                {"verified": False, "reason": "RELAY_RECEIPT_UNREADABLE",
                 "error": str(exc)[:800], "path": str(path)})
    if not isinstance(value, dict):
        return ({"agent": profile["agent"], "event_id": event_id,
                 "state": "RECEIPT_PERSISTENCE_FAILED", "task_delivered": False,
                 "agent_accepted": False, "receipt_file": str(path)},
                {"verified": False, "reason": "RELAY_RECEIPT_NOT_OBJECT",
                 "path": str(path)})
    relay = dict(value)
    relay["receipt_file"] = str(path)
    bound = relay.get("agent") == profile["agent"] and relay.get("event_id") == event_id
    return relay, {
        "verified": bound,
        "reason": "RELAY_RECEIPT_BOUND" if bound else "RELAY_RECEIPT_IDENTITY_MISMATCH",
        "path": str(path),
        "sha256": sha256(raw).hexdigest(),
    }


def execute(*, profile: dict[str, Any], message: str, event_id: str,
            receipt_path: Path, manifest_path: Path, lock_path: Path, driver: Any,
            allow_launch: bool = False, urgent: bool = False,
            session_root: Path | None = None, workspace_id: str | Path = "",
            session_provider: str = "", session_binding_evidence: str = "",
            result_marker: str = "", apply: bool = False) -> dict[str, Any]:
    """Run the established relay once only when ``apply`` is explicitly true.

    Without ``apply`` this is exactly :func:`preview`; callers therefore can
    safely use the same command in change review and production without a
    hidden desktop side effect.
    """
    profile = ui_fallback.normalise_profile(dict(profile))
    if not apply:
        return preview(profile=profile, message=message, event_id=event_id,
                       result_marker=result_marker)
    ui_fallback.run(
        profile, _require_message(message), event_id=_require_event(event_id),
        receipt_path=Path(receipt_path), lock_path=Path(lock_path), driver=driver,
        allow_launch=allow_launch, urgent=urgent, session_root=session_root,
        workspace_id=workspace_id, session_provider=session_provider,
        session_binding_evidence=session_binding_evidence,
    )
    # The receipt is the durable action evidence. Read it back instead of
    # trusting the in-memory return so a partial write cannot look complete.
    enriched, integrity = _persisted_relay(
        profile=profile, event_id=event_id, receipt_path=Path(receipt_path))
    manifest = build_manifest(profile=profile, message=message, event_id=event_id,
                              relay=enriched, result_marker=result_marker,
                              mode="APPLY", receipt_integrity=integrity)
    _atomic_json(Path(manifest_path), manifest)
    return manifest


def reconcile_readback(*, profile: dict[str, Any], message: str, event_id: str,
                       receipt_path: Path, manifest_path: Path,
                       result_marker: str,
                       readback_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Update the final stage from a later, read-only session-bound transcript.

    No driver is accepted here: this path reads an already persisted relay
    receipt plus caller-supplied transcript-bus events and writes a replacement
    acceptance envelope. It cannot type, click, launch, or send a message.
    """
    profile = ui_fallback.normalise_profile(dict(profile))
    _require_message(message)
    _require_event(event_id)
    if not isinstance(result_marker, str) or not result_marker.strip():
        raise ValueError("result_marker is required for readback reconciliation")
    relay, integrity = _persisted_relay(
        profile=profile, event_id=event_id, receipt_path=Path(receipt_path))
    manifest = build_manifest(
        profile=profile, message=message, event_id=event_id, relay=relay,
        result_marker=result_marker, mode="READBACK_RECONCILE",
        receipt_integrity=integrity, readback_events=readback_events,
    )
    _atomic_json(Path(manifest_path), manifest)
    return manifest
