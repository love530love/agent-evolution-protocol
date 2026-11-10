"""Lease-bound CDP delivery for a verified original Agent conversation.

``transport_cdp`` deliberately stays a small stateless DOM primitive.  This
module is the execution-plane wrapper that makes it safe beside the profiled
UIA relay: it requires an original-session profile, takes the shared lease
before a composer can change, verifies the active transcript before input, and
leaves ambiguous post-input outcomes fenced as ``UNCERTAIN``.

It establishes at most ``MESSAGE_VISIBLE``.  An Agent's own reply remains the
only proof of ``AGENT_ACK``.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from agent_evolution_protocol.coordination import session_lease
from agent_evolution_protocol.coordination import transport_cdp
from agent_evolution_protocol.coordination import ui_fallback
from agent_evolution_protocol.federation.locking import LockTimeout


SCHEMA = "aep-cdp-session-delivery-v1"


def _atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _request_sha256(*, profile: dict, text: str, event_id: str, target_id: str) -> str:
    payload = {
        "agent": str(profile.get("agent", "")),
        "session_marker": str(profile.get("session_marker", "")),
        "target_id": target_id,
        "event_id": event_id,
        "text": text,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return sha256(raw).hexdigest()


def _markers(profile: dict, field: str) -> list[str]:
    value = profile.get(field, [])
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def _target_matches_websocket(ws_url: str, target_id: str) -> bool:
    """Prove that the endpoint belongs to the exact CDP page selected.

    A caller normally obtains both values from ``/json/list``.  Keeping only a
    title or passing an unrelated WebSocket URL would quietly turn an "exact
    target" API into a fuzzy one, though: CDP would then faithfully write into
    the wrong Electron tab.  Chromium's page endpoints carry the target id in
    their final ``/devtools/page/<id>`` path component, including when a proxy
    preserves a query string.  Treat a non-standard endpoint as unbound rather
    than guessing.
    """
    try:
        parsed = urlsplit(ws_url)
    except ValueError:
        return False
    if parsed.scheme != "ws" or not parsed.netloc:
        return False
    return unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1]) == target_id.strip()


def _with_lease(payload: dict, *, binding: dict, lease: session_lease.SessionLease,
                lease_root: Path) -> dict:
    result = dict(payload)
    result["schema"] = SCHEMA
    result["session_lease"] = {
        "binding": binding,
        "lease_root": str(lease_root),
        "state_file": str(lease.state_file),
    }
    return result


def _collision_receipt_path(receipt_path: Path, *, event_id: str,
                            request_sha256: str) -> Path:
    """Keep a conflicting receipt immutable and record this outcome beside it.

    A receipt path is part of the forensic trail.  Replacing a receipt that
    belongs to another event would turn a harmless caller mistake into loss of
    the only evidence of what was already attempted.  The short digest avoids
    using a caller-controlled event id as a path component while keeping the
    outcome discoverable next to the requested receipt.
    """
    key = sha256((event_id + "\\0" + request_sha256).encode("utf-8")).hexdigest()[:20]
    return receipt_path.with_name(receipt_path.name + ".collision-" + key + ".json")


def _write_preinput_outcome(receipt_path: Path, payload: dict, *, event_id: str,
                            request_sha256: str) -> dict:
    """Persist a refusal without erasing evidence from a prior attempt.

    Validation/busy failures happen before this adapter writes a composer, but
    they can still be invoked with an event receipt path that already contains
    an earlier route's evidence. Receipt paths are forensic identifiers, not
    scratch files: retain the original and place the new diagnostic beside it.
    """
    result = dict(payload)
    if receipt_path.exists():
        outcome_path = _collision_receipt_path(
            receipt_path, event_id=event_id, request_sha256=request_sha256)
        result["outcome_receipt_path"] = str(outcome_path)
        _atomic(outcome_path, result)
        return result
    _atomic(receipt_path, result)
    return result


def _preflight(ws_url: str, profile: dict) -> dict:
    """Read the exact CDP target before writing a composer."""
    ws = transport_cdp.connect(ws_url)
    try:
        transcript = transport_cdp.read_transcript(ws)
        session_ok = ui_fallback._evidence_present(  # noqa: SLF001 - shared evidence rule
            transcript, str(profile.get("session_marker", "")),
            _markers(profile, "session_evidence_markers"))
        if not session_ok:
            return {"ok": False, "status": "SESSION_NOT_VERIFIED",
                    "transcript_chars": len(transcript)}
        model_ok = ui_fallback._evidence_present(  # noqa: SLF001 - shared evidence rule
            transcript, str(profile.get("model_marker", "")),
            _markers(profile, "model_evidence_markers"))
        if not model_ok:
            return {"ok": False, "status": "MODEL_NOT_VERIFIED",
                    "transcript_chars": len(transcript)}
        composer = transport_cdp.locate_composer(ws)
        if not composer:
            return {"ok": False, "status": "NO_SAFE_COMPOSER",
                    "transcript_chars": len(transcript)}
        return {"ok": True, "status": "PREFLIGHT", "composer": composer,
                "transcript_chars": len(transcript)}
    finally:
        ws.close()


def _session_binding(profile: dict, *, coordination_root: Path, workspace_id: str | Path,
                     session_provider: str, session_binding_evidence: str) -> tuple[dict, str]:
    """Build a cross-route binding; never default a CDP port into its own key."""
    canonical_provider = str(session_provider or profile.get("session_provider") or "").strip()
    if not canonical_provider:
        raise ValueError("CDP delivery requires a verified session_provider shared with every route")
    provider, evidence = ui_fallback.session_binding_provider(
        profile, transport_provider="cdp", explicit_provider=canonical_provider,
        explicit_evidence=session_binding_evidence)
    logical_workspace = str(workspace_id or profile.get("workspace_id")
                            or coordination_root.resolve())
    return session_lease.binding(
        workspace_id=logical_workspace,
        provider=provider,
        target_agent=str(profile.get("agent", "")),
        session_id=str(profile.get("session_marker", "")),
    ), evidence


def deliver(*, coordination_root: Path | str, profile: dict, ws_url: str, target_id: str,
            text: str, event_id: str, receipt_path: Path | str,
            workspace_id: str | Path = "", session_provider: str = "",
            session_binding_evidence: str = "", wait_seconds: float = 8.0,
            lock_seconds: float = session_lease.DEFAULT_LOCK_SECONDS) -> dict:
    """Deliver once through CDP with original-session fencing and readback.

    ``target_id`` is required.  A fuzzy title is a discovery convenience, never
    proof of the tab that owns a conversation.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text is required")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id is required")
    if not isinstance(target_id, str) or not target_id.strip():
        raise ValueError("target_id is required; refuse fuzzy CDP target selection")
    if not isinstance(ws_url, str) or not ws_url.startswith("ws://"):
        raise ValueError("ws_url must be a CDP websocket URL")

    supplied_root = Path(coordination_root)
    lease_root = session_lease.canonical_root(supplied_root)
    canonical_coordination_root = lease_root.parent
    receipt_path = Path(receipt_path)
    request_sha256 = _request_sha256(profile=profile, text=text,
                                     event_id=event_id, target_id=target_id)
    if not _target_matches_websocket(ws_url, target_id):
        return _write_preinput_outcome(receipt_path, {
            "schema": SCHEMA,
            "status": "TARGET_ID_MISMATCH",
            "event_id": event_id,
            "task_delivered": False,
            "agent_accepted": False,
            "reason": "CDP websocket endpoint is not bound to the requested exact target id",
        }, event_id=event_id, request_sha256=request_sha256)
    try:
        binding, binding_evidence = _session_binding(
            profile, coordination_root=canonical_coordination_root,
            workspace_id=workspace_id, session_provider=session_provider,
            session_binding_evidence=session_binding_evidence)
    except ValueError as exc:
        return _write_preinput_outcome(receipt_path, {
            "schema": SCHEMA, "status": "SESSION_BINDING_REQUIRED",
            "event_id": event_id, "task_delivered": False,
            "agent_accepted": False, "error": str(exc),
        }, event_id=event_id, request_sha256=request_sha256)

    legacy_roots = (supplied_root,) if supplied_root != lease_root else ()
    try:
        with session_lease.acquire(lease_root, binding, timeout=float(lock_seconds),
                                   legacy_roots=legacy_roots) as lease:
            if receipt_path.exists():
                try:
                    existing = json.loads(receipt_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    existing = None
                if not isinstance(existing, dict):
                    outcome_path = _collision_receipt_path(
                        receipt_path, event_id=event_id, request_sha256=request_sha256)
                    lease.write(event_id=event_id, request_sha256=request_sha256,
                                state="UNCERTAIN", reason="CDP receipt is unreadable",
                                receipt_file=str(receipt_path), outcome_receipt_file=str(outcome_path),
                                transport_provider="cdp",
                                session_binding_evidence=binding_evidence)
                    result = _with_lease({
                        "status": "SEND_UNCERTAIN", "event_id": event_id,
                        "task_delivered": False, "agent_accepted": False,
                        "reason": "existing CDP receipt is unreadable; inspect original session",
                        "outcome_receipt_path": str(outcome_path),
                    }, binding=binding, lease=lease, lease_root=lease_root)
                    _atomic(outcome_path, result)
                    return result
                if (existing.get("event_id") != event_id
                        or existing.get("request_sha256") != request_sha256):
                    outcome_path = _collision_receipt_path(
                        receipt_path, event_id=event_id, request_sha256=request_sha256)
                    lease.write(event_id=event_id, request_sha256=request_sha256,
                                state="UNCERTAIN", reason="CDP receipt collision",
                                receipt_file=str(receipt_path), outcome_receipt_file=str(outcome_path),
                                transport_provider="cdp",
                                session_binding_evidence=binding_evidence)
                    result = _with_lease({
                        "status": "RECEIPT_COLLISION", "event_id": event_id,
                        "task_delivered": False, "agent_accepted": False,
                        "reason": "receipt path belongs to a different request; no retry performed",
                        "outcome_receipt_path": str(outcome_path),
                    }, binding=binding, lease=lease, lease_root=lease_root)
                    _atomic(outcome_path, result)
                    return result
                return existing

            try:
                preflight = _preflight(ws_url, profile)
            except Exception as exc:  # no composer write has happened yet
                preflight = {"ok": False, "status": "CDP_PREFLIGHT_FAILED",
                             "error": str(exc)[:800]}
            if not preflight.get("ok"):
                lease.write(event_id=event_id, request_sha256=request_sha256,
                            state="PROVEN_NOT_SENT", reason=str(preflight.get("status", "PREFLIGHT_FAILED")),
                            receipt_file=str(receipt_path), transport_provider="cdp",
                            session_binding_evidence=binding_evidence)
                result = _with_lease({
                    "status": str(preflight.get("status", "PREFLIGHT_FAILED")),
                    "event_id": event_id, "task_delivered": False,
                    "agent_accepted": False, "preflight": preflight,
                    "request_sha256": request_sha256,
                }, binding=binding, lease=lease, lease_root=lease_root)
                _atomic(receipt_path, result)
                return result

            lease.write(event_id=event_id, request_sha256=request_sha256,
                        state="SENDING", receipt_file=str(receipt_path),
                        transport_provider="cdp", target_id=target_id,
                        session_binding_evidence=binding_evidence)
            try:
                receipt = transport_cdp.deliver(ws_url, text, event_id=event_id,
                                                wait_seconds=float(wait_seconds))
            except Exception as exc:  # after SENDING, a retry could duplicate input
                receipt = {"status": "CDP_DELIVERY_EXCEPTION", "task_delivered": False,
                           "agent_accepted": False, "error": str(exc)[:800]}

            if receipt.get("task_delivered") is True:
                lease.write(event_id=event_id, request_sha256=request_sha256,
                            state="SETTLED", delivery_state="MESSAGE_VISIBLE",
                            receipt_file=str(receipt_path), transport_provider="cdp",
                            target_id=target_id, session_binding_evidence=binding_evidence)
                result = _with_lease({
                    **receipt, "status": "MESSAGE_VISIBLE", "event_id": event_id,
                    "request_sha256": request_sha256,
                    "note": "same CDP transcript contains the event id; target ACK remains separate",
                }, binding=binding, lease=lease, lease_root=lease_root)
                _atomic(receipt_path, result)
                return result

            # A browser can change between preflight and click.  After SENDING
            # every incomplete result is fenced until original-session evidence
            # reconciles it; an automatic retry could duplicate a task.
            lease.write(event_id=event_id, request_sha256=request_sha256,
                        state="UNCERTAIN", reason=str(receipt.get("status", "UNKNOWN")),
                        receipt_file=str(receipt_path), transport_provider="cdp",
                        target_id=target_id, session_binding_evidence=binding_evidence)
            result = _with_lease({
                **receipt, "event_id": event_id, "task_delivered": False,
                "agent_accepted": False, "request_sha256": request_sha256,
                "note": "post-input CDP outcome lacks same-session visibility; inspect before retry",
            }, binding=binding, lease=lease, lease_root=lease_root)
            _atomic(receipt_path, result)
            return result
    except session_lease.SessionLeaseBlocked as exc:
        return _write_preinput_outcome(receipt_path, {
            "schema": SCHEMA, "status": "SESSION_UNCERTAIN", "event_id": event_id,
            "task_delivered": False, "agent_accepted": False,
            "previous_event_id": exc.previous.get("event_id", ""),
            "reason": "another route left this original session unresolved",
        }, event_id=event_id, request_sha256=request_sha256)
    except LockTimeout:
        return _write_preinput_outcome(receipt_path, {
            "schema": SCHEMA, "status": "SESSION_BUSY", "event_id": event_id,
            "task_delivered": False, "agent_accepted": False,
            "reason": "another route currently holds the original-session lease",
        }, event_id=event_id, request_sha256=request_sha256)
