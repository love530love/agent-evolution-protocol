"""Durable, per-client calibration evidence for a strict desktop route.

Calibration is deliberately *not* delivery. It answers the preceding and
narrower question: given one profiled Agent client, can its current UIA tree
uniquely identify the configured original conversation, model and composer in
four explicitly bounded regions? A successful calibration may later enable a
strict pre-input attestation; it never proves a message was typed, sent,
visible, acknowledged or acted upon.

The module stores geometry/digests only -- never a UI tree, conversation
transcript or message draft.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import uuid4

from agent_evolution_protocol.coordination import session_attestation
from agent_evolution_protocol.coordination import ui_fallback
from agent_evolution_protocol.coordination.foundation import write_json_atomic
from agent_evolution_protocol.federation.locking import workspace_lock


SCHEMA = "aep-client-route-calibration-v1"
_AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")


def _agent(value: str) -> str:
    name = str(value or "").strip().lower()
    if not _AGENT.fullmatch(name):
        raise ValueError("invalid agent")
    return name


def _identifier(value: object) -> str:
    identifier = str(value or "").strip()
    if not _ID.fullmatch(identifier):
        raise ValueError("invalid calibration id")
    return identifier


def _sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def _digest(value: object) -> str:
    text = str(value or "").strip().casefold()
    if text.startswith("sha256:"):
        text = text[len("sha256:"):]
    if not _HEX.fullmatch(text):
        raise ValueError("expected a sha256 digest")
    return "sha256:" + text


def _timestamp(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("calibrated_at is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("calibrated_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def proof_digest(proof: Mapping[str, Any]) -> str:
    """Return the stable identity of one normalized four-region proof."""
    normal = ui_fallback.normalise_session_proof(dict(proof), required=True)
    return _sha256(normal)


def directory(root: str | Path, agent: str) -> Path:
    """Return the append-only calibration directory for one client."""
    return Path(root) / "route_calibrations" / _agent(agent)


def path(root: str | Path, agent: str, calibration_id: str) -> Path:
    """Return the immutable artifact path for one validated calibration ID."""
    return directory(root, agent) / f"{_identifier(calibration_id)}.json"


def _base_profile(profile: Mapping[str, Any], *, agent: str,
                  proof: Mapping[str, Any]) -> dict[str, Any]:
    """Return the policy observed by calibration, excluding its own reference.

    A replacement calibration supersedes the previous reference.  Including a
    prior ``route_calibration`` in the fingerprint would make a perfectly
    ordinary re-calibration impossible to verify later, while removing the
    *current* reference lets us still detect changes to the executable,
    markers, transport, workspace binding, or four proof regions.
    """
    candidate = dict(profile)
    candidate.pop("route_calibration", None)
    candidate.update({
        "agent": agent,
        "prefer_uia3": True,
        "require_strict_session_attestation": True,
        "session_proof": dict(proof),
    })
    return candidate


def build(*, agent: str, profile: Mapping[str, Any], session_proof: Mapping[str, Any],
          observation: Mapping[str, Any], selected_original: bool = False,
          moment: datetime | None = None) -> dict[str, Any]:
    """Build a calibration record only from a successful bounded observation."""
    name = _agent(agent)
    if observation.get("valid") is not True or observation.get("status") != "CALIBRATED":
        raise ValueError("cannot record an invalid route calibration")
    surface = observation.get("surface")
    if not isinstance(surface, Mapping):
        raise ValueError("calibration surface is required")
    if observation.get("composer_candidate_count") != 1:
        raise ValueError("calibration requires exactly one bounded composer")
    proof = ui_fallback.normalise_session_proof(dict(session_proof), required=True)
    observed_proof = ui_fallback.normalise_session_proof(
        observation.get("session_proof"), required=True)
    if observed_proof != proof:
        raise ValueError("calibration observation proof does not match requested proof")
    if bool(selected_original) != bool(observation.get("selected_original", False)):
        raise ValueError("calibration selection evidence does not match observation")
    no_delivery_fields = (
        "desktop_input_performed", "message_visible", "agent_ack",
        "task_result_readback",
    )
    if any(observation.get(field) is not False for field in no_delivery_fields):
        raise ValueError("calibration observation must not contain delivery evidence")
    # The CLI supplies ``load_profile`` output, while library callers may hold
    # the parsed JSON mapping already.  Canonicalize both forms before taking
    # the policy digest so omitted optional fields cannot manufacture drift.
    normal_profile = ui_fallback.normalise_profile(profile)
    candidate = _base_profile(normal_profile, agent=name, proof=proof)
    now = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
    payload = {
        "schema": SCHEMA,
        "id": f"AEP-CAL-{now.strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:12]}",
        "agent": name,
        "calibrated_at": now.isoformat(),
        "status": "CALIBRATED",
        "source": "uia3-bounded-tree",
        "selected_original": bool(selected_original),
        "session_proof": proof,
        "session_proof_digest": proof_digest(proof),
        "profile_digest": session_attestation.profile_digest(candidate, agent=name),
        "surface_digest": session_attestation.surface_digest(surface),
        "desktop_input_performed": False,
        "message_visible": False,
        "agent_ack": False,
        "task_result_readback": False,
        "delivery_verified": False,
    }
    payload["digest"] = _sha256(payload)
    return payload


def validate(payload: Mapping[str, Any], *, agent: str | None = None) -> dict[str, Any]:
    """Validate a stored calibration without asserting present UI state."""
    if not isinstance(payload, Mapping):
        return {"valid": False, "reason": "CALIBRATION_MALFORMED"}
    try:
        if payload.get("schema") != SCHEMA:
            raise ValueError("schema")
        name = _agent(payload.get("agent", ""))
        if agent is not None and name != _agent(agent):
            raise ValueError("agent")
        identifier = _identifier(payload.get("id", ""))
        if payload.get("status") != "CALIBRATED":
            raise ValueError("status")
        _timestamp(payload.get("calibrated_at", ""))
        proof = ui_fallback.normalise_session_proof(payload.get("session_proof"), required=True)
        if _digest(payload.get("session_proof_digest")) != proof_digest(proof):
            raise ValueError("session_proof_digest")
        _digest(payload.get("profile_digest"))
        _digest(payload.get("surface_digest"))
        if any(payload.get(field) is not False for field in (
            "desktop_input_performed", "message_visible", "agent_ack",
            "task_result_readback", "delivery_verified",
        )):
            raise ValueError("delivery_fields")
        expected = dict(payload)
        supplied = _digest(expected.pop("digest", ""))
        if supplied != _sha256(expected):
            raise ValueError("digest")
    except (TypeError, ValueError):
        return {"valid": False, "reason": "CALIBRATION_MALFORMED"}
    return {"valid": True, "reason": "CALIBRATION_VALID", "agent": name,
            "id": identifier, "digest": supplied,
            "session_proof": proof,
            "surface_digest": _digest(payload["surface_digest"]),
            "delivery_verified": False}


def write(root: str | Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Persist an immutable calibration artifact and return a detached copy.

    A read-only recalibration must not replace the artifact referenced by an
    already active strict profile.  One agent therefore has a directory of
    calibration-ID artifacts, not a mutable ``<agent>.json`` slot.  A repeated
    write of the *same* ID is idempotent only when its digest is identical.
    """
    checked = validate(payload)
    if checked["valid"] is not True:
        raise ValueError(f"cannot write invalid route calibration: {checked['reason']}")
    row = dict(payload)
    artifact = path(root, row["agent"], checked["id"])
    lock_path = artifact.with_suffix(".lock")
    with workspace_lock(lock_path):
        if artifact.exists():
            try:
                existing = json.loads(artifact.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"existing calibration artifact is unreadable: {exc}") from exc
            existing_checked = validate(existing, agent=checked["agent"])
            if (existing_checked.get("valid") is not True or
                    existing_checked.get("digest") != checked["digest"]):
                raise ValueError("calibration id already exists with a different payload")
            return dict(existing)
        write_json_atomic(artifact, row, indent=2)
    return row


def validate_profile_reference(root: str | Path, profile: Mapping[str, Any]) -> dict[str, Any]:
    """Verify that a strict UIA profile still points to its exact artifact.

    This is a runtime gate, not just a profile-schema check.  It prevents a
    hand-authored or deleted calibration record from authorizing a strict UIA
    send.  Non-strict/legacy profiles deliberately return ``required=False``:
    their existing routes retain their own admission rules.
    """
    strict = profile.get("require_strict_session_attestation") is True
    preferred = profile.get("prefer_uia3") is True
    if not (strict and preferred):
        return {"valid": True, "required": False, "reason": "CALIBRATION_NOT_REQUIRED"}
    try:
        name = _agent(profile.get("agent", ""))
        proof = ui_fallback.normalise_session_proof(profile.get("session_proof"), required=True)
        reference = profile.get("route_calibration")
        if not isinstance(reference, Mapping):
            raise ValueError("CALIBRATION_REFERENCE_MISSING")
        record_path = path(root, name, str(reference.get("id", "")))
        try:
            stored = json.loads(record_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ValueError("CALIBRATION_ARTIFACT_MISSING") from None
        except (OSError, json.JSONDecodeError):
            raise ValueError("CALIBRATION_ARTIFACT_UNREADABLE") from None
        checked = validate(stored, agent=name)
        if checked.get("valid") is not True:
            raise ValueError("CALIBRATION_ARTIFACT_INVALID")
        expected = {
            "schema": SCHEMA,
            "id": checked["id"],
            "digest": checked["digest"],
            "session_proof_digest": proof_digest(proof),
            "surface_digest": checked["surface_digest"],
            "calibrated_at": str(stored["calibrated_at"]),
        }
        if dict(reference) != expected:
            raise ValueError("CALIBRATION_REFERENCE_MISMATCH")
        base = _base_profile(profile, agent=name, proof=proof)
        if session_attestation.profile_digest(base, agent=name) != _digest(stored["profile_digest"]):
            raise ValueError("CALIBRATION_PROFILE_CHANGED")
    except (TypeError, ValueError) as exc:
        reason = str(exc or "CALIBRATION_REFERENCE_INVALID")
        return {"valid": False, "required": True, "reason": reason,
                "delivery_verified": False}
    return {
        "valid": True,
        "required": True,
        "reason": "CALIBRATION_REFERENCE_VALID",
        "agent": name,
        "id": checked["id"],
        "digest": checked["digest"],
        "path": str(record_path),
        "delivery_verified": False,
    }


def apply_profile(root: str | Path, profile_path: str | Path,
                  payload: Mapping[str, Any]) -> dict[str, Any]:
    """Atomically opt one validated calibration into a client UI profile.

    This is intentionally a separate explicit operation. A successful
    read-only calibration never changes future delivery policy by itself.
    """
    checked = validate(payload)
    if checked["valid"] is not True:
        raise ValueError(f"cannot apply invalid route calibration: {checked['reason']}")
    source = Path(profile_path)
    artifact = path(root, checked["agent"], checked["id"])
    # The lock makes concurrent AEP calibrators serialize their read/compare/
    # replace cycle.  ``write_json_atomic`` alone prevents torn files but not a
    # lost update where two agents each overwrite the other's valid profile.
    lock_path = source.with_name(f".{source.name}.calibration.lock")
    with workspace_lock(lock_path):
        try:
            stored = json.loads(artifact.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ValueError("calibration artifact is missing; write it before applying") from None
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"calibration artifact is unreadable: {exc}") from exc
        stored_checked = validate(stored, agent=checked["agent"])
        if (stored_checked.get("valid") is not True or
                stored_checked.get("digest") != checked["digest"]):
            raise ValueError("persisted calibration artifact does not match the requested apply")
        try:
            current = ui_fallback.load_profile(source)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"cannot read profile: {exc}") from exc
        if _agent(current.get("agent", "")) != checked["agent"]:
            raise ValueError("profile agent does not match route calibration")
        pre_adoption = _base_profile(current, agent=checked["agent"],
                                      proof=checked["session_proof"])
        if (session_attestation.profile_digest(pre_adoption, agent=checked["agent"]) !=
                _digest(payload["profile_digest"])):
            raise ValueError("profile has changed since route calibration")
        candidate = dict(current)
        candidate.update({
            "prefer_uia3": True,
            "require_strict_session_attestation": True,
            "session_proof": checked["session_proof"],
            "route_calibration": {
                "schema": SCHEMA,
                "id": checked["id"],
                "digest": checked["digest"],
                "session_proof_digest": proof_digest(checked["session_proof"]),
                "surface_digest": checked["surface_digest"],
                "calibrated_at": str(payload["calibrated_at"]),
            },
        })
        # Validate the candidate before replacing the operator-owned profile.
        temporary = source.with_name(f".{source.name}.{uuid4().hex}.calibration.json")
        try:
            write_json_atomic(temporary, candidate, indent=2)
            ui_fallback.load_profile(temporary)
        finally:
            temporary.unlink(missing_ok=True)
        write_json_atomic(source, candidate, indent=2)
        return candidate
