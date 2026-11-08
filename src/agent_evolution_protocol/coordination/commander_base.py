"""Commander foundations: constants, persistent state, and order queueing.

This module holds the pieces of ``commander`` that have **no dependency on the
desktop-transport surface** and therefore cannot take part in the
dispatch <-> surface recursion.  Keeping them separate means ``commander_uia``
(the transport surface) can import this module without creating an import cycle.

All JSON writes go through :mod:`foundation.io` (the single canonical atomic
writer) and every timestamp comes from :func:`foundation.ids.iso_now`, so the
order-inbox format and time shape stay identical to the pre-split code.  The
public behaviour of every function below is byte-for-byte preserved; see
``docs/ARCHITECTURE_REVIEW_20261108.zh-CN.md`` (commander split design).
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256  # noqa: F401 - re-exported for legacy callers
from pathlib import Path
import re
import time
from uuid import uuid4

from agent_evolution_protocol.coordination import client_registry as registry
from agent_evolution_protocol.coordination import desktop_providers
from agent_evolution_protocol.coordination import dispatch_contract
from agent_evolution_protocol.coordination import dispatch_gate
from agent_evolution_protocol.coordination import overlay_audit
from agent_evolution_protocol.coordination import workspace_identity
from agent_evolution_protocol.coordination.foundation import (
    iso_now,
    read_json,
    write_json_atomic,
)
from agent_evolution_protocol.coordination.overlay_detector import classify
from agent_evolution_protocol.coordination.provider_readback import (
    accepted as readback_accepted,
    receipt as make_readback,
)

# --- constants (single source; commander.py re-imports these) -----------------
SAFE_AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ACK_TOKEN = "AEP-ACK"
CEASE_AFTER_FAILURES = 3
REINFORCE_AFTER_MINUTES = 20  # 主订单停火或久未ACK后，允许候选agent补位
# A deferral that proves nothing was typed is a "not yet", not a failure.  The
# scheduler retries those on its own, but only within a bounded budget and only
# after a backoff, so an automatic retry can never turn into a blind replay.
AUTO_RESUME_LIMIT = 6
AUTO_RESUME_BACKOFF_SECONDS = 20

# Deferrals caused by the *executor's environment* rather than the target's
# readiness.  These prove nothing was typed, so retrying them cannot duplicate a
# message, and they do not consume the retry budget: the condition clears when
# the environment changes, not when we stop trying.
ENVIRONMENTAL_DEFERRALS = frozenset({"EXECUTOR_TOO_LOW"})

# One switch per order.  A second would mean the continuation surface failed for
# the same reason, and bouncing between two surfaces forever is not retrying.
SURFACE_SWITCH_LIMIT = 1

# DEPOSITED -> WINDOW_FOUND -> INPUT_SUBMITTED -> MESSAGE_VISIBLE -> AGENT_ACK
ORDER_STATES = ("DEPOSITED", "DEFERRED", "DISPATCHING", "WINDOW_FOUND", "INPUT_SUBMITTED", "MESSAGE_VISIBLE", "AGENT_ACK", "CEASED", "FAILED", "RETIRED")

# --- canonical IO / time (aliases keep call sites identical) -------------------
def _root(base: Path) -> Path:
    return Path(base) / "commander"


def _atomic(path: Path, payload: dict) -> None:
    write_json_atomic(path, payload, indent=1)


def _read(path: Path) -> dict:
    result = read_json(path, default=None)
    return result if isinstance(result, dict) else {}


def now() -> str:
    return iso_now()


def provider_readback(provider: str, event_id: str, raw: dict) -> dict:
    """Normalize adapter receipts; visibility never becomes an ACK.

    The default is "not sent", not "sent".  This used to be a whitelist of
    failure statuses with everything else falling through to
    ``INPUT_SUBMITTED``, which meant every status nobody had taught it about --
    ``urgent-interject-unavailable``, ``composer-not-confirmed``,
    ``composer-confirmed-no-send``, ``LAUNCH_FORBIDDEN`` -- was recorded as
    proof that input had been entered.  A receipt has to earn
    ``INPUT_SUBMITTED``: either the adapter says the text was verified in the
    composer, or it reports an unconfirmed submit.  Anything else is ``FAILED``
    with the status kept as the error, so the reason survives.
    """
    if not isinstance(raw, dict):
        return make_readback(provider=provider, event_id=event_id, state="UNKNOWN", observed=False, error="adapter receipt is not an object")
    if raw.get("agent_accepted") is True:
        state, ack = "AGENT_ACK", True
    elif raw.get("task_delivered") is True:
        state, ack = "MESSAGE_VISIBLE", False
    elif raw.get("input_verified") is True or raw.get("composer_verified") is True \
            or raw.get("status") == "submitted-unconfirmed":
        state, ack = "INPUT_SUBMITTED", False
    else:
        state, ack = "FAILED", False
    value = make_readback(provider=provider, event_id=event_id, state=state,
                          observed=bool(raw.get("task_delivered") or raw.get("agent_accepted")),
                          ack=ack, evidence=list(raw.get("evidence", [])) if isinstance(raw.get("evidence", []), list) else [],
                          error=str(raw.get("error", "")) or (str(raw.get("status", "")) if state == "FAILED" else ""))
    value["raw"] = raw
    value["accepted"] = readback_accepted(value)
    return value


def submit_order(root: Path, *, target: str, task: str, text: str, sender: str,
                 priority: str = "normal", event_id: str = "", allow_launch: bool = False,
                 transport: str = "auto", expires_minutes: int = 120,
                 candidates: str = "", source_wake: dict | None = None,
                 workspace_id: str = "", urgent_interject: bool = False) -> dict:
    """Queue a directive for one client. Nothing is sent until a tick runs.

    ``candidates`` is a comma/space separated list of backup agents. When the
    primary order stalls (ceasefire or long-unacked), a tick may spawn a
    differentiated "reinforcement" order for one of them instead of blindly
    re-sending to the same target -- the fire-musket volley: one wave falls back
    to reload while the next wave steps up.
    """
    if not SAFE_AGENT.fullmatch(target) or not SAFE_AGENT.fullmatch(sender):
        raise ValueError("invalid agent name")
    if not text.strip():
        raise ValueError("text is required")
    if source_wake is not None and not isinstance(source_wake, dict):
        raise ValueError("source_wake must be an object")
    if workspace_id:
        workspace_identity.validate_workspace_id(workspace_id)
    order_id = now().replace(":", "").replace(".", "").replace("+", "") + "-" + uuid4().hex[:8]
    event = event_id or ("EVT-" + uuid4().hex[:10].upper())
    # A generic instruction such as "include AEP-ACK" is not an event
    # acknowledgement.  Only skip augmentation when this exact event id is
    # already present; otherwise the dispatch contract cannot prove that a
    # reply belongs to this order.
    body = text if event in text else f"{text.rstrip()}\n\n[{ACK_TOKEN}:{event}]"
    created_at = now()
    expires_at = datetime.fromtimestamp(time.time() + expires_minutes * 60, timezone.utc).isoformat()
    dispatch = dispatch_contract.build(
        order_id=order_id,
        event_id=event,
        workspace_id=workspace_id,
        sender=sender,
        target=target,
        task=task or "GENERAL",
        instruction=body,
        priority=priority,
        created_at=created_at,
        expires_at=expires_at,
        session_mode="sticky",
        allow_new_session=False,
        legacy_compat=not bool(workspace_id),
    )
    payload = {
        "id": order_id, "event_id": event, "target": target, "sender": sender,
        "task": task or "GENERAL", "priority": priority, "text": body, "allow_launch": allow_launch,
        "transport": transport, "urgent_interject": bool(urgent_interject), "state": "DEPOSITED", "created_at": created_at,
        "expires_at": expires_at,
        "attempts": [], "ack": None,
        "candidates": _parse_candidates(candidates), "children": [], "parent": None,
        "dispatch": dispatch,
    }
    if event not in body:
        raise ValueError("event id must appear in the message so read-back can verify it")
    # Record the hash of the exact bytes that will be typed.  A gate that runs at
    # send time but has nothing to compare against can only check the text
    # against itself; this is what makes "the message was edited after it was
    # queued" a detectable condition instead of an invisible one.
    payload["content_sha256"] = dispatch_gate.content_sha256(body)
    if source_wake is not None:
        # Persist provenance with the first order write, not in a second update
        # that could be lost if the wake ingress crashes between writes.
        payload["source_wake"] = deepcopy(source_wake)
    if workspace_id:
        payload["workspace_id"] = workspace_id
    _atomic(_root(root) / "inbox" / f"{order_id}.json", payload)
    return payload


def _orders(root: Path) -> list[dict]:
    folder = _root(root) / "inbox"
    if not folder.exists():
        return []
    return [_read(p) for p in sorted(folder.glob("*.json"))]


def _state_path(root: Path, order_id: str) -> Path:
    return _root(root) / "state" / f"{order_id}.json"


def _record_attempt(root: Path, order: dict, *, state: str, transport: str = "",
                    receipt: dict | None = None, note: str = "") -> dict:
    if state in {"DEFERRED", "CEASED"} and (Path(root) / "desktop_providers").is_dir():
        try:
            alternatives = desktop_providers.plan(root, operation="locate", previous_state=(
                "PROVEN_NOT_SENT" if state == "DEFERRED" else "UNKNOWN"))
            receipt = {**(receipt or {}), "desktop_provider_plan": alternatives}
        except (OSError, ValueError, KeyError, TypeError):
            # An optional provider registry must not prevent storing the order receipt.
            pass
    attempt = {"at": now(), "state": state, "transport": transport,
               "receipt": receipt or {}, "note": note}
    raw = receipt or {}
    # Adapters may provide a normalized overlay observation. Persist it in a
    # separate append-only stream without making audit failure affect delivery.
    if raw.get("overlay_type") or raw.get("overlay_title"):
        try:
            observed = classify(control_type=str(raw.get("overlay_type", "")),
                                title=str(raw.get("overlay_title", "")),
                                text=str(raw.get("overlay_text", "")),
                                actions=raw.get("overlay_actions", []),
                                source=str(raw.get("overlay_source", transport)))
            overlay_audit.record(Path(root), observed, agent=order["target"],
                                 action=str(raw.get("overlay_action", "")),
                                 verified=bool(raw.get("overlay_verified", False)),
                                 event_id=order.get("event_id", ""))
        except (OSError, TypeError, ValueError):
            pass
    order.setdefault("attempts", []).append(attempt)
    order["state"] = state
    order["updated_at"] = now()
    if state == "AGENT_ACK":
        order["ack"] = {"at": now(), "evidence": (receipt or {}).get("evidence", "")}
    _atomic(_state_path(root, order["id"]), order)
    _atomic(_root(root) / "inbox" / f"{order['id']}.json", order)
    return order


def _ceased(root: Path, order: dict) -> bool:
    failures = 0
    for attempt in reversed(order.get("attempts", [])):
        if attempt.get("state") in ("FAILED", "INPUT_SUBMITTED", "submitted-unconfirmed"):
            failures += 1
        elif attempt.get("state") in ("MESSAGE_VISIBLE", "AGENT_ACK"):
            break
        if failures >= CEASE_AFTER_FAILURES:
            return True
    return False


def _deferred_count(order: dict) -> int:
    """How many deferrals this order has accumulated in total.

    Counting the tail would reset on every automatic retry -- the retry writes
    a DEPOSITED attempt first -- and the budget would never be reached, which
    turns a bounded retry into an unbounded loop.

    Environmental deferrals are excluded.  ``EXECUTOR_TOO_LOW`` proves that
    nothing was typed: UIPI refused the input before it reached the window.  It
    is a fact about the executor's integrity level, not about the target's
    readiness, so retrying it cannot duplicate a message and waiting on a person
    is the wrong response -- a scheduler that gives up after six attempts at
    60-second intervals declares failure eleven minutes into an outage that a
    single elevation would have ended.  The order's ``expires_at`` is the real
    bound, and it is the right one: it bounds "the environment might change",
    which is what this is.
    """
    total = 0
    for attempt in order.get("attempts", []):
        if attempt.get("state") != "DEFERRED":
            continue
        if (attempt.get("receipt") or {}).get("status") in ENVIRONMENTAL_DEFERRALS:
            continue
        total += 1
    return total


def _auto_resume_due(order: dict) -> bool:
    """Should the scheduler retry this deferred order without being asked?

    Only ``wait_for_ready`` deferrals used to be re-examined, so every other
    pre-input deferral parked the order until a person typed ``resume``.  That
    is a queue, not a loop: measured on this workspace, a client that was busy
    for ninety seconds left the order at DEFERRED forever even though the
    target had gone idle.

    Three conditions keep the automatic retry from becoming a blind replay:
    the durable history must prove nothing was typed (``_preinput_retryable``),
    the deferral count must stay under ``AUTO_RESUME_LIMIT``, and
    ``AUTO_RESUME_BACKOFF_SECONDS`` must have elapsed since the last attempt.
    """
    if order.get("state") != "DEFERRED":
        return False
    if _deferred_count(order) >= AUTO_RESUME_LIMIT:
        return False
    if not _preinput_retryable(order):
        return False
    last = (order.get("attempts") or [{}])[-1]
    try:
        at = datetime.fromisoformat(str(last.get("at", "")))
        elapsed = (datetime.now(timezone.utc) - at).total_seconds()
    except (TypeError, ValueError):
        # An unreadable timestamp is not evidence of a recent attempt; the
        # pre-input proof above is what actually authorises the retry.
        return True
    return elapsed >= AUTO_RESUME_BACKOFF_SECONDS


def _preinput_retryable(order: dict) -> bool:
    """Return True only when the durable history proves no input was sent.

    ``WINDOW_FOUND`` is recorded immediately before the profiled adapter
    performs its readiness check.  It is therefore retryable only when a
    later attempt explicitly records a pre-input deferral.  A bare
    ``WINDOW_FOUND`` remains uncertain and must not be replayed.
    """
    def _receipt_proves_preinput(receipt: dict) -> bool:
        """Read a profiled relay receipt when the outer order only has a link.

        A PowerShell parameter-binding error happens before the UI script can
        focus, paste, or click.  Preserve the normal no-replay fence, but make
        this one machine-verifiable class eligible for an explicit retry.
        """
        if not isinstance(receipt, dict) or receipt.get("task_delivered", False):
            return False
        error = str(receipt.get("error", ""))
        markers = ("WINDOW_NOT_FULLY_VISIBLE", "COMPOSER_HINT_", "WINDOW_RECT_UNAVAILABLE",
                   "WINDOW_FOCUS_DENIED", "SESSION_LABEL_", "MODEL_MARKER_",
                   "ParameterBindingException", "multiple times specified the parameter 'ComposerHints'",
                   "parameter 'ComposerHints'")
        if (any(marker in error for marker in markers)
                or ("ComposerHints" in error and ("绑定参数" in error or "指定了参数" in error))):
            return True
        receipt_file = receipt.get("receipt_file")
        if not receipt_file:
            return False
        try:
            detail = read_json(Path(str(receipt_file)))
        except (OSError, TypeError, ValueError):
            return False
        if not isinstance(detail, dict):
            return False
        if detail.get("task_delivered", False):
            return False
        report = detail.get("send_report") or {}
        if report.get("status") == "composer-not-confirmed" and not report.get("task_delivered", False):
            return True
        detail_error = str(detail.get("error", ""))
        return detail.get("state") == "SEND_UNCERTAIN" and (
            any(marker in detail_error for marker in markers)
            or ("ComposerHints" in detail_error and
                ("绑定参数" in detail_error or "指定了参数" in detail_error))
        )

    attempts = order.get("attempts", [])
    for index, attempt in enumerate(attempts):
        state = attempt.get("state")
        if state in {"DEPOSITED", "DEFERRED", "DISPATCHING"}:
            continue
        if state == "WINDOW_FOUND":
            later = attempts[index + 1:]
            proven_preinput = any(
                row.get("state") in {"DEFERRED", "CEASED"}
                and not row.get("receipt", {}).get("task_delivered", False)
                and (
                    row.get("receipt", {}).get("status") in {
                    "AGENT_BUSY", "URGENT_INTERJECT_UNAVAILABLE", "ACTIVITY_UNCONFIRMED", "NO_WINDOW",
                    "LAUNCH_UNVERIFIED", "WINDOW_OR_INPUT_AMBIGUOUS",
                    "NO_SAFE_COMPOSER", "SESSION_NOT_VERIFIED", "MODEL_NOT_VERIFIED",
                    "EXECUTOR_TOO_LOW", "COMPOSER_NOT_CONFIRMED",
                    }
                    or _receipt_proves_preinput(row.get("receipt", {}))
                    or (
                        row.get("receipt", {}).get("status") == "PROFILE_DELIVERY_FAILED"
                        and any(marker in str(row.get("receipt", {}).get("error", ""))
                                for marker in ("WINDOW_NOT_FULLY_VISIBLE", "COMPOSER_HINT_",
                                               "WINDOW_RECT_UNAVAILABLE", "WINDOW_FOCUS_DENIED",
                                               "SESSION_LABEL_", "MODEL_MARKER_"))
                    )
                )
                for row in later
            )
            if proven_preinput:
                continue
        if state == "CEASED":
            receipt = attempt.get("receipt", {})
            error = str(receipt.get("error", ""))
            if (_receipt_proves_preinput(receipt)
                    or (receipt.get("status") == "PROFILE_DELIVERY_FAILED"
                    and not receipt.get("task_delivered", False)
                    and any(marker in error for marker in (
                        "WINDOW_NOT_FULLY_VISIBLE", "COMPOSER_HINT_",
                        "WINDOW_RECT_UNAVAILABLE", "WINDOW_FOCUS_DENIED",
                        "SESSION_LABEL_", "MODEL_MARKER_",
                    )))):
                continue
        return False
    return True


def _parse_candidates(raw: object) -> list[str]:
    """Split a comma/space separated candidate string into safe agent names."""
    if not raw:
        return []
    items = [a.strip().lower() for a in re.split(r"[,\s]+", str(raw)) if a.strip()]
    return [a for a in items if SAFE_AGENT.fullmatch(a)]
