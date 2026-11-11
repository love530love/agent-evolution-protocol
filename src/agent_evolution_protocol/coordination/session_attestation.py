"""Durable, short-lived proof that a delivery target is the intended session.

An attestation is deliberately a *precondition*, not a delivery receipt.  It
answers the narrow question: "immediately before input, did an adapter observe
the configured original conversation on this surface?"  It never proves that
text was sent, became visible, received an ACK, or produced work.  Those facts
belong to the delivery acceptance chain.

The file is designed to survive a coordinator restart without allowing stale
window observations to be mistaken for a current conversation.  A caller must
re-attest after the short lease expires, after a profile changes, or after the
observable surface changes (including a new PID).
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from agent_evolution_protocol.coordination import process_info


SCHEMA = "aep-original-session-attestation-v1"
# A session proof is a precondition for putting text into a live third-party
# conversation.  Keeping the default at the strict lease length avoids the
# surprising (and unsafe) outcome where ``build(strict=True)`` rejects its own
# default argument.
DEFAULT_TTL_SECONDS = 300
STRICT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 3600

REASON_VALID = "ATTESTATION_VALID"
REASON_ABSENT = "ATTESTATION_ABSENT"
REASON_SCHEMA = "ATTESTATION_SCHEMA_MISMATCH"
REASON_AGENT = "ATTESTATION_AGENT_MISMATCH"
REASON_PROFILE = "ATTESTATION_PROFILE_MISMATCH"
REASON_FINGERPRINT = "ATTESTATION_FINGERPRINT_MISMATCH"
REASON_SURFACE = "ATTESTATION_SURFACE_MISMATCH"
REASON_EXPIRED = "ATTESTATION_EXPIRED"
REASON_FUTURE_TIMESTAMP = "ATTESTATION_FUTURE_TIMESTAMP"
REASON_MALFORMED_TIMESTAMP = "ATTESTATION_MALFORMED_TIMESTAMP"
REASON_INVALID_EVIDENCE = "ATTESTATION_EVIDENCE_INVALID"
REASON_INVALID_STRICT_EVIDENCE = "ATTESTATION_STRICT_EVIDENCE_INVALID"
REASON_INVALID = "ATTESTATION_INVALID"

# A narrow character set makes the filename derivation safe and keeps one agent
# from accidentally (or deliberately) overwriting another agent's attestation.
_AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)

PROFILE_FIELDS = (
    "agent",
    "process",
    "process_name",
    "title",
    "window_title",
    "window_titles",
    "exe",
    "allowed_exe_roots",
    "session_marker",
    "model_marker",
    "session_evidence_markers",
    "model_evidence_markers",
    "transport",
    "workspace_id",
    "session_provider",
)
_PROFILE_PATH_FIELDS = {"exe", "allowed_exe_roots"}

SURFACE_FIELDS = (
    "kind",
    "pid",
    "title",
    "actual_exe",
    "path_match_mode",
    "path_policy_digest",
    "target_id",
)
_SURFACE_PATH_FIELDS = {"actual_exe"}

# Whole-window or sidebar OCR can find an old marker while the composer belongs
# to another conversation.  Strict mode therefore accepts only evidence tied to
# active transcript content.
STRICT_MARKER_SCOPES = frozenset({"active-transcript", "ocr-content-region"})
_STRICT_SWITCH_RESULTS = frozenset({"selected", "already-active", "verified-active"})


def _agent_key(agent: str) -> str:
    value = str(agent or "").strip()
    if not _AGENT_RE.fullmatch(value):
        raise ValueError("agent must be a non-empty safe identifier")
    return value.casefold()


def _normalise_text(value: Any) -> str:
    return str(value or "").strip()


def _normalise_path(value: Any) -> str:
    text = _normalise_text(value)
    if not text:
        return ""
    text = text.replace("/", "\\")
    while "\\\\" in text:
        text = text.replace("\\\\", "\\")
    return text.rstrip("\\").casefold()


def _stable_value(value: Any, *, path: bool = False) -> Any:
    """Return a JSON-native canonical value without retaining caller objects."""
    if isinstance(value, Mapping):
        return {str(key): _stable_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple, set, frozenset)):
        values = [_stable_value(item, path=path) for item in value]
        # Profile lists are policy sets (allowed roots, candidate titles,
        # evidence markers), so their declaration order must not invalidate a
        # still-identical profile.
        return sorted(values, key=lambda item: json.dumps(item, ensure_ascii=False,
                                                           sort_keys=True, separators=(",", ":")))
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    return _normalise_path(value) if path else _normalise_text(value)


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def _normalise_sha256(value: Any) -> str:
    text = _normalise_text(value).casefold()
    if text.startswith("sha256:"):
        text = text[len("sha256:"):]
    if not _HEX_RE.fullmatch(text):
        raise ValueError("expected a sha256 digest")
    return "sha256:" + text


def _session_fingerprint(value: str | Mapping[str, Any]) -> str:
    if isinstance(value, Mapping):
        value = value.get("fingerprint", "")
    text = _normalise_text(value).casefold()
    if not _HEX_RE.fullmatch(text):
        raise ValueError("observed session fingerprint must be a SHA-256 hex digest")
    return text


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("moment must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _now(moment: datetime | None = None) -> datetime:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _parse_timestamp(value: Any) -> datetime:
    text = _normalise_text(value)
    if not _TIMESTAMP_RE.fullmatch(text):
        raise ValueError("timestamp must be ISO-8601 with an explicit timezone")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _profile_for_agent(profile: Mapping[str, Any], agent: str) -> dict[str, Any]:
    if not isinstance(profile, Mapping):
        raise ValueError("profile must be an object")
    agent_key = _agent_key(agent)
    declared = _normalise_text(profile.get("agent"))
    if declared and _agent_key(declared) != agent_key:
        raise ValueError("profile agent does not match attestation agent")

    payload: dict[str, Any] = {}
    for field in PROFILE_FIELDS:
        raw = agent_key if field == "agent" else profile.get(field, "")
        if field == "allowed_exe_roots":
            raw = process_info.normalise_allowed_exe_roots(raw)
        payload[field] = _stable_value(raw, path=field in _PROFILE_PATH_FIELDS)
    # Bind any profile-supplied evidence policy without copying raw evidence
    # into an attestation file.  This lets a profile change invalidate a lease
    # without making evidence contents a second unreviewed message channel.
    payload["session_binding_evidence_sha256"] = _sha256_json(
        _stable_value(profile.get("session_binding_evidence", ""))
    )
    return payload


def profile_digest(profile: Mapping[str, Any], *, agent: str | None = None) -> str:
    """Digest the stable profile policy that names an original conversation."""
    selected = agent or _normalise_text(profile.get("agent"))
    if not selected:
        raise ValueError("agent is required to bind a profile digest")
    return _sha256_json(_profile_for_agent(profile, selected))


def _marker_digest(marker: Any) -> str:
    value = _normalise_text(marker)
    if not value:
        raise ValueError("session and model markers must be non-empty")
    return _sha256_json({"marker": value})


def canonical_surface(surface: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical, restart-sensitive identity of the observed UI/CDP surface."""
    if not isinstance(surface, Mapping):
        raise ValueError("surface must be an object")
    result: dict[str, Any] = {}
    for field in SURFACE_FIELDS:
        if field == "pid":
            raw_pid = surface.get(field, 0)
            if isinstance(raw_pid, bool):
                raise ValueError("surface pid must be a positive integer")
            try:
                pid = int(raw_pid)
            except (TypeError, ValueError) as exc:
                raise ValueError("surface pid must be a positive integer") from exc
            if pid <= 0:
                raise ValueError("surface pid must be a positive integer")
            result[field] = pid
        else:
            result[field] = _stable_value(surface.get(field, ""),
                                          path=field in _SURFACE_PATH_FIELDS)

    for required in ("kind", "title", "actual_exe"):
        if not result[required]:
            raise ValueError(f"surface {required} is required")
    if result["path_policy_digest"]:
        result["path_policy_digest"] = _normalise_sha256(result["path_policy_digest"])
    return result


def surface_digest(surface: Mapping[str, Any]) -> str:
    return _sha256_json(canonical_surface(surface))


def directory(coord_root: str | os.PathLike[str]) -> Path:
    return Path(coord_root) / "session_attestations"


def path(coord_root: str | os.PathLike[str], agent: str) -> Path:
    return directory(coord_root) / f"{_agent_key(agent)}.json"


def _evidence(profile: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, Mapping):
        raise ValueError("evidence must be an object")
    result = {
        "capture_sha256": _normalise_sha256(evidence.get("capture_sha256", "")),
        "transcript_source": _normalise_text(evidence.get("transcript_source")),
        "marker_scope": _normalise_text(evidence.get("marker_scope")),
        "session_marker_seen": evidence.get("session_marker_seen"),
        "model_marker_seen": evidence.get("model_marker_seen"),
        "session_switch": _stable_value(evidence.get("session_switch", {})),
        "focus_changed": evidence.get("focus_changed"),
        "input_attempted": evidence.get("input_attempted"),
        "input_submitted": evidence.get("input_submitted"),
        "evidence_path": _normalise_text(evidence.get("evidence_path")),
        "marker_binding": {
            "session_marker_digest": _marker_digest(profile.get("session_marker")),
            "model_marker_digest": _marker_digest(profile.get("model_marker")),
        },
    }
    return result


def _evidence_reason(
    evidence: Any,
    profile: Mapping[str, Any] | None = None,
    *,
    strict: bool,
) -> str | None:
    if not isinstance(evidence, Mapping):
        return REASON_INVALID_STRICT_EVIDENCE if strict else REASON_INVALID_EVIDENCE
    try:
        _normalise_sha256(evidence.get("capture_sha256", ""))
    except ValueError:
        return REASON_INVALID_STRICT_EVIDENCE if strict else REASON_INVALID_EVIDENCE
    if not _normalise_text(evidence.get("transcript_source")) or not _normalise_text(
        evidence.get("marker_scope")
    ):
        return REASON_INVALID_STRICT_EVIDENCE if strict else REASON_INVALID_EVIDENCE
    for key in ("session_marker_seen", "model_marker_seen", "focus_changed",
                "input_attempted", "input_submitted"):
        if type(evidence.get(key)) is not bool:
            return REASON_INVALID_STRICT_EVIDENCE if strict else REASON_INVALID_EVIDENCE
    if evidence["session_marker_seen"] is not True or evidence["model_marker_seen"] is not True:
        return REASON_INVALID_STRICT_EVIDENCE if strict else REASON_INVALID_EVIDENCE
    # An attestation must predate typing.  A post-input view might be a new
    # conversation selected as a side effect of the very operation it is meant
    # to authorise.
    if evidence["input_attempted"] or evidence["input_submitted"]:
        return REASON_INVALID_STRICT_EVIDENCE if strict else REASON_INVALID_EVIDENCE
    switch = evidence.get("session_switch")
    if not isinstance(switch, Mapping) or type(switch.get("attempted")) is not bool or \
            type(switch.get("unique")) is not bool or not _normalise_text(switch.get("result")):
        return REASON_INVALID_STRICT_EVIDENCE if strict else REASON_INVALID_EVIDENCE
    binding = evidence.get("marker_binding")
    if not isinstance(binding, Mapping):
        return REASON_INVALID_STRICT_EVIDENCE if strict else REASON_INVALID_EVIDENCE
    try:
        session_digest = _normalise_sha256(binding.get("session_marker_digest", ""))
        model_digest = _normalise_sha256(binding.get("model_marker_digest", ""))
    except ValueError:
        return REASON_INVALID_STRICT_EVIDENCE if strict else REASON_INVALID_EVIDENCE
    if profile is not None:
        try:
            if session_digest != _marker_digest(profile.get("session_marker")) or \
                    model_digest != _marker_digest(profile.get("model_marker")):
                return REASON_INVALID_STRICT_EVIDENCE if strict else REASON_INVALID_EVIDENCE
        except ValueError:
            return REASON_INVALID_STRICT_EVIDENCE if strict else REASON_INVALID_EVIDENCE
    if strict:
        if _normalise_text(evidence["marker_scope"]) not in STRICT_MARKER_SCOPES:
            return REASON_INVALID_STRICT_EVIDENCE
        if switch["unique"] is not True or _normalise_text(switch["result"]) not in _STRICT_SWITCH_RESULTS:
            return REASON_INVALID_STRICT_EVIDENCE
        if switch["attempted"] and _normalise_text(switch["result"]) == "already-active":
            return REASON_INVALID_STRICT_EVIDENCE
    return None


def build(
    *,
    agent: str,
    profile: Mapping[str, Any],
    observed_fingerprint: str | Mapping[str, Any],
    surface: Mapping[str, Any],
    evidence: Mapping[str, Any],
    moment: datetime | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    strict: bool = False,
) -> dict[str, Any]:
    """Build a fresh attestation; callers may atomically :func:`write` it.

    ``strict`` creates an attestation suitable for direct original-session UI
    input: the marker must be proven in active transcript content and the
    session selection evidence must establish uniqueness.  Non-strict rows are
    still useful for observation/audit, but a direct-input adapter should ask
    :func:`validate` for strict validation.
    """
    agent_key = _agent_key(agent)
    if isinstance(ttl_seconds, bool):
        raise ValueError("ttl_seconds must be an integer")
    try:
        ttl = int(ttl_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("ttl_seconds must be an integer") from exc
    maximum = STRICT_TTL_SECONDS if strict else MAX_TTL_SECONDS
    if ttl <= 0 or ttl > maximum:
        raise ValueError(f"ttl_seconds must be between 1 and {maximum}")

    observed_at = _now(moment)
    profile_row = _profile_for_agent(profile, agent_key)
    payload = {
        "schema": SCHEMA,
        "agent": agent_key,
        "observed_at": _timestamp(observed_at),
        "expires_at": _timestamp(observed_at + timedelta(seconds=ttl)),
        "profile_digest": _sha256_json(profile_row),
        "session_fingerprint": _session_fingerprint(observed_fingerprint),
        "surface": canonical_surface(surface),
        "evidence": _evidence(profile, evidence),
    }
    payload["surface_digest"] = _sha256_json(payload["surface"])
    reason = _structural_reason(payload, now=observed_at, profile=profile,
                                strict=strict)
    if reason:
        raise ValueError(f"cannot build session attestation: {reason}")
    return payload


def _structural_reason(
    payload: Any,
    *,
    now: datetime,
    profile: Mapping[str, Any] | None,
    strict: bool,
) -> str | None:
    if not isinstance(payload, Mapping):
        return REASON_ABSENT
    if payload.get("schema") != SCHEMA:
        return REASON_SCHEMA
    try:
        _agent_key(payload.get("agent", ""))
    except ValueError:
        return REASON_AGENT
    try:
        _normalise_sha256(payload.get("profile_digest", ""))
        _session_fingerprint(payload.get("session_fingerprint", ""))
        canonical = canonical_surface(payload.get("surface", {}))
        if _normalise_sha256(payload.get("surface_digest", "")) != _sha256_json(canonical):
            return REASON_SURFACE
    except ValueError:
        return REASON_INVALID
    try:
        observed_at = _parse_timestamp(payload.get("observed_at", ""))
        expires_at = _parse_timestamp(payload.get("expires_at", ""))
    except ValueError:
        return REASON_MALFORMED_TIMESTAMP
    if observed_at > now:
        return REASON_FUTURE_TIMESTAMP
    max_ttl = STRICT_TTL_SECONDS if strict else MAX_TTL_SECONDS
    if expires_at <= observed_at or expires_at - observed_at > timedelta(seconds=max_ttl):
        return REASON_MALFORMED_TIMESTAMP
    if expires_at <= now:
        return REASON_EXPIRED
    return _evidence_reason(payload.get("evidence"), profile, strict=strict)


def _result(valid: bool, reason: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload = payload if isinstance(payload, Mapping) else {}
    return {
        "valid": valid,
        "reason": reason,
        "agent": payload.get("agent", ""),
        "observed_at": payload.get("observed_at", ""),
        "expires_at": payload.get("expires_at", ""),
        "profile_digest": payload.get("profile_digest", ""),
        "session_fingerprint": payload.get("session_fingerprint", ""),
        # Explicitly protect callers from interpreting a precondition as an
        # outcome; a delivery acceptance module owns those later facts.
        "delivery_proven": False,
    }


def validate(
    payload: Mapping[str, Any] | None,
    *,
    agent: str,
    profile: Mapping[str, Any],
    observed_fingerprint: str | Mapping[str, Any],
    surface: Mapping[str, Any],
    strict: bool = False,
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Validate an attestation against one current profile/fingerprint/surface.

    This function is intentionally fail-closed.  A missing, malformed, stale,
    future-dated, or merely non-strict record cannot authorise UI input.
    """
    now = _now(moment)
    if payload is None:
        return _result(False, REASON_ABSENT)
    # Check the self-contained record before comparing it to the *current*
    # profile.  Otherwise a changed marker can be reported as weak evidence,
    # masking the more actionable fact that the profile itself changed and the
    # lease must be renewed.
    reason = _structural_reason(payload, now=now, profile=None, strict=strict)
    if reason:
        return _result(False, reason, payload)

    try:
        agent_key = _agent_key(agent)
    except ValueError:
        return _result(False, REASON_AGENT, payload)
    if _agent_key(payload.get("agent", "")) != agent_key:
        return _result(False, REASON_AGENT, payload)
    try:
        expected_profile = profile_digest(profile, agent=agent_key)
    except ValueError:
        return _result(False, REASON_PROFILE, payload)
    if _normalise_sha256(payload.get("profile_digest", "")) != expected_profile:
        return _result(False, REASON_PROFILE, payload)
    evidence_reason = _evidence_reason(payload.get("evidence"), profile, strict=strict)
    if evidence_reason:
        return _result(False, evidence_reason, payload)
    try:
        if _session_fingerprint(payload.get("session_fingerprint", "")) != \
                _session_fingerprint(observed_fingerprint):
            return _result(False, REASON_FINGERPRINT, payload)
    except ValueError:
        return _result(False, REASON_FINGERPRINT, payload)
    try:
        if _normalise_sha256(payload.get("surface_digest", "")) != surface_digest(surface):
            return _result(False, REASON_SURFACE, payload)
    except ValueError:
        return _result(False, REASON_SURFACE, payload)
    return _result(True, REASON_VALID, payload)


def write(
    coord_root: str | os.PathLike[str],
    agent: str,
    payload: Mapping[str, Any],
    *,
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Durably write one valid attestation with atomic replacement.

    The temporary file is fsynced before ``os.replace``.  Readers therefore see
    either the preceding complete JSON object or the complete new one, never a
    partially-written proof.
    """
    agent_key = _agent_key(agent)
    now = _now(moment)
    reason = _structural_reason(payload, now=now, profile=None, strict=False)
    if reason:
        raise ValueError(f"cannot write invalid session attestation: {reason}")
    if _agent_key(payload.get("agent", "")) != agent_key:
        raise ValueError("cannot write attestation for a different agent")

    target = path(coord_root, agent_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    data = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return dict(payload)


def read(coord_root: str | os.PathLike[str], agent: str) -> dict[str, Any] | None:
    """Read a stored object; malformed or absent files are deliberately absent."""
    target = path(coord_root, agent)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def validate_stored(
    coord_root: str | os.PathLike[str],
    *,
    agent: str,
    profile: Mapping[str, Any],
    observed_fingerprint: str | Mapping[str, Any],
    surface: Mapping[str, Any],
    strict: bool = False,
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Read then validate the current agent's stored attestation."""
    return validate(
        read(coord_root, agent),
        agent=agent,
        profile=profile,
        observed_fingerprint=observed_fingerprint,
        surface=surface,
        strict=strict,
        moment=moment,
    )
