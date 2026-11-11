"""Unattended command loop: discover, perceive, dispatch, verify.

One tick answers three questions the legion keeps getting wrong:
  1. What is each client showing right now?  (perceive -> transcript bus)
  2. Which orders are still undelivered, and can we prove delivery? (dispatch)
  3. Did the target answer with an ACK, or are we mistaking a focused window
     for a working agent? (verify)

Transport order is cheapest-proof-first: CDP when the client exposes a DOM,
otherwise the UIA/OCR adapter. Every transition is written to disk as a receipt
so "we sent it" is never inferred from "we ran a command".
"""
from __future__ import annotations

from datetime import datetime, timezone
from copy import deepcopy
from hashlib import sha256
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
import time
from uuid import uuid4

from agent_evolution_protocol.coordination import client_registry as registry
from agent_evolution_protocol.coordination import transcript_bus as bus
from agent_evolution_protocol.coordination import transport_cdp as cdp
from agent_evolution_protocol.coordination import cdp_session_delivery
from agent_evolution_protocol.coordination import uia_argv
from agent_evolution_protocol.coordination import ui_fallback
from agent_evolution_protocol.coordination import desktop_providers
from agent_evolution_protocol.coordination import desktop_health
from agent_evolution_protocol.coordination import overlay_audit
from agent_evolution_protocol.coordination import dispatch_contract
from agent_evolution_protocol.coordination import dispatch_gate
from agent_evolution_protocol.coordination import ack_protocol
from agent_evolution_protocol.coordination import session_fingerprint
from agent_evolution_protocol.coordination.overlay_detector import classify
from agent_evolution_protocol.coordination.provider_readback import receipt as make_readback, accepted as readback_accepted
from agent_evolution_protocol.coordination.workspace_identity import validate_workspace_id

# The order state machine (constants + helpers) and the desktop-transport
# surface now live in ``commander_base`` / ``commander_uia``.  They are
# re-exported here so every existing ``from ...commander import <name>`` and
# ``commander.<name>`` call site keeps working unchanged.
from .commander_base import (
    ACK_TOKEN,
    AUTO_RESUME_BACKOFF_SECONDS,
    AUTO_RESUME_LIMIT,
    CEASE_AFTER_FAILURES,
    ENVIRONMENTAL_DEFERRALS,
    ORDER_STATES,
    REINFORCE_AFTER_MINUTES,
    SAFE_AGENT,
    SURFACE_SWITCH_LIMIT,
    _atomic,
    _auto_resume_due,
    _ceased,
    _deferred_count,
    _orders,
    _parse_candidates,
    _preinput_retryable,
    _read,
    _record_attempt,
    _root,
    _state_path,
    now,
    provider_readback,
    submit_order,
)
from .commander_uia import (
    _capture_uia,
    _cdp_port_owned_by_anchor,
    _continuation_surfaces,
    _pick_transport,
    _profiled_deliver,
    _profiled_readiness,
    _pwsh,
    _uia_deliver,
    _uia_observe,
    perceive,
)

# A deferral that proves nothing was typed is a "not yet", not a failure.  The
# scheduler retries those on its own, but only within a bounded budget and only
# after a backoff, so an automatic retry can never turn into a blind replay.

# Deferrals caused by the *executor's environment* rather than the target's
# readiness.  These prove nothing was typed, so retrying them cannot duplicate a
# message, and they do not consume the retry budget: the condition clears when
# the environment changes, not when we stop trying.

# One switch per order.  A second would mean the continuation surface failed for
# the same reason, and bouncing between two surfaces forever is not retrying.

# DEPOSITED -> WINDOW_FOUND -> INPUT_SUBMITTED -> MESSAGE_VISIBLE -> AGENT_ACK














































def _switch_surface(root: Path, order: dict, *, blocked_target: str,
                    transport: str, receipt: dict) -> dict | None:
    """Deliver on a continuation surface instead of waiting for a person.

    Only ever called after a receipt that *proves nothing was typed* -- UIPI
    refused the input before it reached the window -- so choosing another
    surface here cannot send the message twice.  That proof is the whole reason
    the switch is allowed without asking: a switch after an uncertain send would
    be a duplicate, and a switch after a proven non-send is a retry.

    Returns the re-dispatched result, or ``None`` when there is nowhere to go.
    """
    if int(order.get("surface_switches") or 0) >= SURFACE_SWITCH_LIMIT:
        return None
    candidates = _continuation_surfaces(root, order, blocked_target=blocked_target)
    if not candidates:
        return None
    chosen = candidates[0]
    order["surface_switches"] = int(order.get("surface_switches") or 0) + 1
    order["target"] = chosen["agent"]
    _record_attempt(
        root, order, state="DEPOSITED", transport=transport,
        receipt={"surface_switch": {"from": blocked_target, "to": chosen["agent"],
                                    "pid": chosen["pid"],
                                    "window_title": chosen["window_title"],
                                    "continuity": chosen["continuity"],
                                    "observed": chosen["observed"],
                                    "blocked_receipt_status": receipt.get("status", "")},
                 "alternatives": [{key: item[key] for key in ("agent", "pid", "window_title")}
                                  for item in candidates]},
        note=(f"SURFACE_SWITCHED: {blocked_target} cannot be driven from this integrity "
              f"level and nothing was typed; continuing the same conversation on "
              f"{chosen['agent']} without asking again"))
    return _dispatch(root, order=_persisted_order(root, order["id"]))


def _dispatch_lock(root: Path, order_id: str):
    from .battle_orders import _task_writer_lock
    if not isinstance(order_id, str) or not order_id:
        raise ValueError("valid order id required")
    # Never use a caller-controlled ID as a directory component.
    key = "commander-dispatch-" + sha256(order_id.encode("utf-8")).hexdigest()
    return _task_writer_lock(Path(root), key)


def _persisted_order(root: Path, order_id: str) -> dict:
    current = next((row for row in _orders(root) if row.get("id") == order_id), None)
    if current is None:
        raise ValueError("persisted order missing or unreadable; no input attempted")
    return current


def _mark_dispatching(root: Path, order: dict) -> dict:
    """Fence a first send without manufacturing a user-visible attempt."""
    fenced = deepcopy(order)
    fenced["state"] = "DISPATCHING"
    fenced["updated_at"] = now()
    _atomic(_state_path(root, fenced["id"]), fenced)
    _atomic(_root(root) / "inbox" / f"{fenced['id']}.json", fenced)
    return fenced




def _workspace_state(root: Path, order: dict) -> dict | None:
    """The federation state that owns this order's task, when it can be read.

    Read-only and best-effort.  A coordination root and a federation root are
    allowed to be different places, so an unreadable workspace is reported as
    "not checked" by the gate instead of being treated as a mismatch -- turning
    "I could not look" into "it is wrong" is how a safety check starts refusing
    correct work.
    """
    workspace_id = str(order.get("workspace_id", "")).strip()
    if not workspace_id:
        return None
    try:
        from agent_evolution_protocol.federation import engine
        return engine.load(Path(root), workspace_id)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _gate_refusal(root: Path, order: dict, *, profile_row: dict,
                  window: dict) -> dict | None:
    """Run the pre-send gate; return a terminal refusal or ``None`` to proceed.

    The state is ``FAILED``, not ``DEFERRED``, on purpose: content that does not
    match its own order will not become correct by waiting, and a retryable
    state would put it back in the queue to be refused again every tick.
    """
    verdict = dispatch_gate.check(
        order, profile=profile_row, workspace=_workspace_state(root, order),
        newer_orders=_orders(root), window=window)
    if verdict["verdict"] == "send":
        return None
    return _record_attempt(
        root, order, state="FAILED",
        receipt={**verdict, "signal": "dispatch_refused"},
        note=f"DISPATCH_REFUSED:{verdict['reason']}: {verdict['detail']}")


def dispatch(root: Path, *, order: dict) -> dict:
    if str(order.get("task", "")).startswith("improvement:"):
        from .battle_orders import _task_writer_lock
        with _task_writer_lock(Path(root), "improvement-dispatch"):
            current = next((row for row in _orders(root) if row.get("id") == order.get("id")), None)
            if current is None:
                raise ValueError("persisted improvement order missing or unreadable; no input attempted")
            if current and current != order:
                # A queued/direct caller may hold any stale receipt, not just an
                # old recipient. Return durable progress without replaying input.
                return current
            return _dispatch(root, order=order)
    with _dispatch_lock(root, order.get("id")):
        current = _persisted_order(root, order["id"])
        if current != order:
            return current
        if current.get("state") != "DEPOSITED" or not _preinput_retryable(current):
            return current
        if datetime.now(timezone.utc) > datetime.fromisoformat(current["expires_at"]):
            return _record_attempt(root, order, state="FAILED", note="EXPIRED")
        # Keep the lock through delivery and durably fence process death before
        # touching discovery/launch/transport. A later caller must not replay a
        # send just because the final adapter receipt was never written.
        order = _mark_dispatching(root, order)
        try:
            result = _dispatch(root, order=order)
        except Exception as exc:
            return _record_attempt(root, order, state="CEASED",
                                   receipt={"status": "DISPATCH_ERROR", "error": str(exc)},
                                   note="DISPATCH_UNCONFIRMED: inspect before any retry")
        if result.get("state") == "DEPOSITED":
            # Launch is pre-input work, but readiness needs explicit rechecking;
            # do not repeatedly launch the client from subsequent ticks.
            return _record_attempt(root, result, state="DEFERRED",
                                   note="CLIENT_LAUNCHED: explicitly resume after readiness")
        return result


def _dispatch(root: Path, *, order: dict) -> dict:
    retired = next((row for row in _orders(root) if row.get("id") == order.get("id")
                    and row.get("state") == "RETIRED"), None)
    if retired:
        return retired
    if order.get("state") == "RETIRED":
        return order
    # Revalidate improvement stage immediately before any client discovery/input.
    if str(order.get("task", "")).startswith("improvement:"):
        from . import improvement
        try:
            proposal = improvement.status(root, order["task"].split(":", 1)[1])
            expected = "EVT-IMPROVE-" + proposal["events"][-1]["sha256"][:24].upper()
            if order.get("event_id") != expected:
                return _record_attempt(root, order, state="CEASED",
                                       note="STALE_IMPROVEMENT_STAGE: no desktop input attempted")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return _record_attempt(root, order, state="CEASED",
                                   note=f"INVALID_IMPROVEMENT: {exc}; no desktop input attempted")
        if order.get("state") not in {"DEPOSITED", "DEFERRED"} or any(
                attempt.get("state") not in {"DEPOSITED", "DEFERRED"}
                for attempt in order.get("attempts", [])):
            # Even direct API calls cannot replay an uncertain/completed send.
            return order
        from . import improvement_work
        try:
            lease = improvement_work.read_lease(root, order["task"].split(":", 1)[1], optional=True)
        except (OSError, ValueError, KeyError, TypeError):
            return _record_attempt(root, order, state="CEASED",
                                   note="INVALID_IMPROVEMENT_LEASE: inspect before delivery")
        if lease.get("active") and lease["head"] == proposal["events"][-1]["sha256"] and lease["expires"] > improvement_work._now():
            return _record_attempt(root, order, state="DEFERRED",
                                   note="IMPROVEMENT_ALREADY_CLAIMED: coordinate with owner before delivery")
    target = order["target"]
    try:
        profile_row = registry.profile(root, target)
    except ValueError:
        return _record_attempt(root, order, state="DEFERRED",
                               note="UNREGISTERED_CLIENT: register a verified client profile before delivery")
    if profile_row.get("out_of_scope"):
        # Refuse before discovery, not after: an out-of-scope surface is not a
        # window that happens to be closed, it is one that must never be typed
        # into.  Naming it here means a future caller cannot register it back
        # into service by accident.
        return _record_attempt(root, order, state="FAILED", note=(
            "OUT_OF_SCOPE_TARGET: " + str(profile_row["out_of_scope"])))
    # ``clients/<agent>.json`` names the client; ``ui_profiles/<agent>.json``
    # names the session inside it.  The fingerprint below is supposed to answer
    # "same conversation as last time", which needs both -- reading only the
    # client record hashed two empty marker strings and could not tell a
    # switched conversation from the original one.
    ui_profile_row = session_fingerprint.ui_profile(root, target)
    profile_row = session_fingerprint.merged_profile(profile_row, ui_profile_row)
    client = None
    try:
        discovered = registry.discover(root, target)
        desktop_health.record(root, component="native-window-enumeration", status="READY")
    except Exception as exc:
        health = desktop_health.record(root, component="native-window-enumeration",
                                       status="TIMEOUT" if "timeout" in str(exc).lower() else "ERROR",
                                       error=str(exc))
        return _record_attempt(root, order, state="DEFERRED",
                               receipt={"desktop_health": health},
                               note="DESKTOP_ENUM_DEGRADED: use browser/CDP or recover the desktop helper before explicit resume")
    for row in discovered["clients"]:
        if row["agent"] == target:
            client = row
            break
    windows = (client or {}).get("windows", [])
    if not windows:
        if not order.get("allow_launch"):
            return _record_attempt(root, order, state="DEFERRED", note="NO_WINDOW: client is not open and launch is not authorised")
        launched = registry.launch(root, target, allow=True)
        if not launched.get("launched"):
            return _record_attempt(root, order, state="DEFERRED", receipt=launched, note="LAUNCH_REFUSED")
        _record_attempt(root, order, state="DEPOSITED", note=f"launched pid={launched.get('pid')}")
        return order

    if not client["unique"] and not client["anchored"]:
        return _record_attempt(root, order, state="DEFERRED",
                               note=f"WINDOW_NOT_UNIQUE: {len(windows)} windows; refusing to guess")

    window = next((row for row in windows if row.get("is_anchor")), windows[0])
    # Which session this is, before anything is typed into it.  A marker check
    # answers "is the string present", which is not the same question: the
    # marker also appears in the sidebar and in the other instance's session
    # list, so a message can land in the right client's wrong conversation while
    # every marker matches.
    observed_identity = session_fingerprint.observe(
        agent=target, window=window, profile=profile_row)
    identity = session_fingerprint.compare(
        session_fingerprint.read(root, target), observed_identity)
    if identity["match"] is False:
        return _record_attempt(
            root, order, state="DEFERRED",
            receipt={"session_fingerprint": identity, "observed": observed_identity},
            note=f"SESSION_FINGERPRINT_MISMATCH: {identity['reason']}")
    strict_attestation = ui_profile_row.get("require_strict_session_attestation", False)
    if type(strict_attestation) is not bool:
        return _record_attempt(
            root, order, state="DEFERRED",
            note="ORIGINAL_SESSION_PROFILE_INVALID: require_strict_session_attestation must be a boolean",
        )
    if strict_attestation and identity["match"] is not True:
        return _record_attempt(
            root, order, state="DEFERRED",
            receipt={"session_fingerprint": identity, "observed": observed_identity},
            note=("SESSION_FINGERPRINT_REQUIRED: strict original-session delivery "
                  "requires a recorded matching fingerprint before input"),
        )
    # Last gate before anything is typed.  It runs here, not inside a transport,
    # because "which text goes where" is a property of the order, and a check
    # implemented per transport is a check that will be missing from the next
    # transport someone adds.
    refused = _gate_refusal(root, order, profile_row=profile_row, window=window)
    if refused is not None:
        return refused
    # A PID identifies a disposable window, not the conversation.  It is only
    # used above to bind a live CDP port to the selected process.  ACK and
    # readback records must use the stable original-session marker instead.
    profile_path = Path(root) / "ui_profiles" / f"{target}.json"
    if not profile_path.is_file():
        return _record_attempt(
            root, order, state="DEFERRED",
            note=("ORIGINAL_SESSION_PROFILE_REQUIRED: no verified UI profile; "
                  "refusing raw UIA/CDP input into a guessed conversation"),
        )
    if strict_attestation:
        try:
            checked_profile = ui_fallback.load_profile(profile_path)
            if checked_profile.get("agent") != target:
                raise ValueError("UI_PROFILE_AGENT_MISMATCH")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return _record_attempt(
                root, order, state="DEFERRED",
                note=f"ORIGINAL_SESSION_PROFILE_INVALID: {exc}; no desktop input attempted",
            )
    order["session_id"] = str(profile_row.get("session_marker") or "").strip()
    if strict_attestation:
        # CDP has no DOM-bound equivalent of the calibrated active-session
        # UIA proof yet.  Never let a strict profile bypass that proof merely
        # because an automatic picker found a debug port.
        if order.get("transport") == "cdp":
            return _record_attempt(
                root, order, state="DEFERRED",
                note=("ATTESTATION_TRANSPORT_UNSUPPORTED: strict original-session "
                      "delivery needs profiled UIA evidence, not a CDP bypass"),
            )
        transport, info = "profiled-ui", {"strict_session_attestation_required": True}
    else:
        transport, info = _pick_transport(root, target, order.get("transport", "auto"))
    _record_attempt(root, order, state="WINDOW_FOUND", transport=transport,
                    receipt={**info, "session_fingerprint": identity})

    if transport == "cdp":
        try:
            cdp_profile = ui_fallback.load_profile(profile_path)
            if cdp_profile.get("agent") != target:
                raise ValueError("UI_PROFILE_AGENT_MISMATCH")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return _record_attempt(
                root, order, state="DEFERRED", transport=transport,
                note=f"ORIGINAL_SESSION_PROFILE_INVALID: {exc}; no CDP input attempted",
            )
        receipt = cdp_session_delivery.deliver(
            coordination_root=root, profile=cdp_profile,
            ws_url=str(info["target"]["ws"]), target_id=str(info["target"].get("id", "")),
            text=order["text"], event_id=order["event_id"],
            receipt_path=_root(root) / "receipts" / f"{order['event_id']}-{target}-cdp.json",
            workspace_id=str(order.get("workspace_id", "")), wait_seconds=8,
        )
        if receipt.get("task_delivered"):
            return _record_attempt(root, order, state="MESSAGE_VISIBLE", transport=transport, receipt=receipt)
        if receipt.get("status") in {
                "SESSION_BINDING_REQUIRED", "SESSION_NOT_VERIFIED", "MODEL_NOT_VERIFIED",
                "NO_SAFE_COMPOSER", "CDP_PREFLIGHT_FAILED", "SESSION_BUSY",
                "TARGET_ID_MISMATCH"}:
            return _record_attempt(root, order, state="DEFERRED", transport=transport,
                                   receipt=receipt,
                                   note="PREINPUT_NOT_READY: CDP original-session preflight is incomplete")
        if receipt.get("status") in {"SESSION_UNCERTAIN", "SEND_UNCERTAIN", "RECEIPT_COLLISION"}:
            return _record_attempt(root, order, state="CEASED", transport=transport,
                                   receipt=receipt,
                                   note="ORIGINAL_SESSION_UNRESOLVED: inspect before any retry")
        return _record_attempt(root, order, state="INPUT_SUBMITTED", transport=transport, receipt=receipt,
                               note="typed into the composer but the event id never appeared; not a delivery")

    receipt = _profiled_deliver(root, target, order["text"], order["event_id"],
                                allow_launch=bool(order.get("allow_launch")),
                                urgent_interject=bool(order.get("urgent_interject")),
                                workspace_id=str(order.get("workspace_id", "")))
    transport = "profiled-ui"
    if transport == "profiled-ui" and receipt.get("agent_accepted") and receipt.get("task_delivered"):
        return _record_attempt(root, order, state="AGENT_ACK", transport=transport, receipt=receipt,
                               note="challenge-response verified by profiled original-session relay")
    if receipt.get("task_delivered"):
        return _record_attempt(root, order, state="MESSAGE_VISIBLE", transport=transport, receipt=receipt)
    # A missing/failed adapter receipt proves neither typing nor sending.
    # Even when the adapter reports a submitted input, never retry blindly:
    # it may have reached the agent without a readable echo.
    if receipt.get("status") in ("AGENT_BUSY", "URGENT_INTERJECT_UNAVAILABLE", "ACTIVITY_UNCONFIRMED", "NO_WINDOW",
                                  "LAUNCH_UNVERIFIED", "WINDOW_OR_INPUT_AMBIGUOUS",
                                  "NO_SAFE_COMPOSER", "SESSION_NOT_VERIFIED", "MODEL_NOT_VERIFIED",
                                  "SESSION_ATTESTATION_REQUIRED",
                                  "EXECUTOR_TOO_LOW", "COMPOSER_NOT_CONFIRMED",
                                  "composer-not-confirmed"):
        note = "PREINPUT_NOT_READY: observe again after the target becomes ready"
        if receipt.get("status") == "EXECUTOR_TOO_LOW":
            # UIPI proves no input reached the window, which makes this the one
            # point in the whole delivery path where another surface can be
            # chosen with no risk of sending twice.  Asking a person here would
            # be a prompt in the middle of an unattended run, so we look for the
            # same conversation elsewhere and continue there instead.
            switched = _switch_surface(root, order, blocked_target=target,
                                       transport=transport, receipt=receipt)
            if switched is not None:
                return switched
            note = ("EXECUTOR_TOO_LOW: UIPI refuses input from a lower integrity level; "
                    "nothing was typed -- no other surface carries this conversation, "
                    "so it is retried until the executor or the target changes")
        return _record_attempt(root, order, state="DEFERRED", transport=transport, receipt=receipt,
                               note=note)
    if receipt.get("status") in ("NO_RECEIPT", "ADAPTER_ERROR", "ADAPTER_MISSING", "PROFILE_DELIVERY_FAILED", "SEND_CONTROL_NOT_FOUND", "send-control-not-found", "input-not-confirmed") or receipt.get("returncode", 0) != 0:
        return _record_attempt(root, order, state="CEASED", transport=transport, receipt=receipt,
                               note="DELIVERY_UNPROVEN: adapter failed; manual diagnosis required")
    return _record_attempt(root, order, state="CEASED", transport=transport, receipt=receipt,
                           note="SUBMISSION_UNCONFIRMED: possible send; do not retry automatically")


def _record_late_ack(root: Path, order: dict, result: dict) -> dict | None:
    """Keep "a reply arrived after the sign-off" without moving the order.

    The reply is a real event and deleting it would be the same class of lie as
    the state overwrite, so it is stored -- in ``late_acks``, which nothing
    schedules from -- while ``state`` stays ``ABANDONED``.  Measured before this
    existed: a sign-off followed by a properly attributed reply left the order
    at ``AGENT_ACK``, ``ack_protocol.abandoned()`` returned ``None`` because it
    reads the state, and the next supervisor step completed the task as
    ``done``.  One machine event had quietly revoked a human decision and
    turned the guarantee into its opposite.

    Idempotent by event id, because ``check_acks`` runs every tick and an
    append-only list of the same reply would otherwise grow without bound.
    """
    evidence = result.get("evidence") or {}
    entry = {"at": now(), "event": str(evidence.get("event", "")),
             "recorded_at": str(evidence.get("recorded_at", "")),
             "session_id": str(evidence.get("session_id", "")),
             "token": str(result.get("token", ""))}
    rows = order.setdefault("late_acks", [])
    if any(str(row.get("event", "")) == entry["event"] for row in rows if isinstance(row, dict)):
        return None
    rows.append(entry)
    order["updated_at"] = now()
    _atomic(_state_path(root, order["id"]), order)
    _atomic(_root(root) / "inbox" / f"{order['id']}.json", order)
    return entry


def check_acks(root: Path) -> dict:
    """Accept only a role-attributed reply event, never a raw screen snapshot.

    The judgement itself lives in ``ack_protocol``: it is the one place that
    knows the four guards, and keeping it out of the loop means a second caller
    cannot reimplement three of them.  What stays here is the state transition
    and the receipt.
    """
    acked = []
    late = []
    orders = _orders(root)
    for order in orders:
        # CEASED means the delivery attempt stopped without an ACK; it does
        # not mean the event can never be acknowledged.  A desktop agent may
        # answer after the bounded read-back window.  Re-checking a late
        # same-event reply is read-only and never replays input, so it is safe
        # to upgrade CEASED -> AGENT_ACK when the anchored transcript proves
        # the event.  FAILED remains terminal because it denotes a hard
        # adapter/input failure rather than an uncertain send.
        if order.get("state") in ("AGENT_ACK", "FAILED", "RETIRED"):
            continue
        result = ack_protocol.verdict(
            order, events=bus.read_events(root, agent=order["target"], limit=500))
        if not result["accepted"]:
            continue
        if ack_protocol.abandoned(order) is not None:
            # A person ended this wait.  Their decision is not a cache entry a
            # later event may invalidate: the reply is recorded beside the
            # sign-off instead of on top of it.  See _record_late_ack for what
            # happened when the order was upgraded here.
            entry = _record_late_ack(root, order, result)
            if entry is not None:
                late.append({"order": order["id"], "target": order["target"],
                             "token": result["token"], "event": entry["event"]})
            continue
        _record_attempt(root, order, state="AGENT_ACK",
                        receipt={"evidence": result["token"],
                                 "event_id": result["evidence"]["event"],
                                 "ack_verdict": result},
                        note="role-attributed agent reply in the anchored session")
        acked.append({"order": order["id"], "target": order["target"],
                      "token": result["token"]})
    return {"at": now(), "acked": acked, "late": late, "checked": len(orders)}


def ack_state(root: Path, order_id: str) -> dict:
    """The ACK verdict for one order, with the rejections that did not count.

    A bare "not acked" tells an operator nothing they can act on; "the token
    appeared in another conversation" tells them the message went somewhere
    else, and "the token appeared as outgoing" tells them they are reading their
    own request back.
    """
    order = next((row for row in _orders(root) if row.get("id") == order_id), None)
    if order is None:
        raise ValueError(f"no order {order_id}")
    return ack_protocol.verdict(
        order, events=bus.read_events(root, agent=order["target"], limit=500))


def abandon(root: Path, order_id: str, *, reason: str, actor: str) -> dict:
    """End the wait for one order, on a named person's signature.

    "No ACK, no progress" has no ceiling, and that is deliberate: a timeout that
    advanced the DAG would delete the guarantee and turn it into a schedule.
    What it lacked was a way out that is not a lie, so this is it -- and it costs
    a name and a written reason, because the record has to answer "who decided to
    stop waiting, and why" long after everyone has forgotten.

    The consequence is *blocked*, never *done*.  Abandoning a delivery is not
    evidence that the work happened, and the only thing worse than a task stuck
    in ``running`` is a task marked finished because someone got tired of waiting.

    Abandoning an acknowledged order is refused: there is a real answer on
    record, and hiding it behind "we gave up" would be a downgrade of the record.
    Abandoning twice is idempotent and keeps the *first* signature, because the
    first one is the decision and the second one is a retry.
    """
    if not isinstance(order_id, str) or not order_id.strip():
        raise ValueError("order_id is required")
    said = str(reason or "").strip()
    if not said:
        raise ValueError("a reason is required: 'we gave up' is not a record")
    who = str(actor or "").strip()
    if not who:
        raise ValueError("an actor is required: an anonymous abandonment is not a decision")

    order = next((row for row in _orders(root) if row.get("id") == order_id), None)
    if order is None:
        raise ValueError(f"no order {order_id}")
    events = bus.read_events(root, agent=order.get("target", ""), limit=500)
    if ack_protocol.abandoned(order) is not None:
        value = ack_protocol.verdict(order, events=events)
        value["already_abandoned"] = True
        return value
    if str(order.get("state", "")) == "AGENT_ACK":
        raise ValueError("order already acknowledged; there is no wait to abandon")

    order["abandoned_by"] = who
    order["abandoned_at"] = now()
    order["abandon_reason"] = said
    # ``_record_attempt`` overwrites ``state``, so without this the sign-off would
    # delete the only record that the message was ever delivered -- and the
    # verdict would then report ``not_sent`` about an order that was sent.  That
    # is the same class of false statement this release exists to remove.
    order["state_before_abandon"] = str(order.get("state", ""))
    _record_attempt(root, order, state=ack_protocol.ABANDONED_STATE,
                    note=f"human decision by {who}: {said}")
    value = ack_protocol.verdict(order, events=events)
    value["already_abandoned"] = False
    return value




def _pick_reinforcement_candidate(order: dict, others: list[dict]) -> str | None:
    """Pure selector: next backup agent not yet used as a reinforcement child."""
    raw = order.get("candidates")
    if isinstance(raw, list):
        candidates = [a for a in raw if isinstance(a, str) and SAFE_AGENT.fullmatch(a)]
    else:
        candidates = _parse_candidates(raw)
    if not candidates:
        return None
    used = {o.get("target") for o in others if o.get("parent") == order.get("id")}
    for cand in candidates:
        if cand != order.get("target") and cand not in used:
            return cand
    return None


def reinforce(root: Path, order_id: str) -> dict | None:
    """Spawn a differentiated backup order when the primary has stalled.

    Trigger: primary is CEASED (ceasefire) OR has sat in WINDOW_FOUND /
    MESSAGE_VISIBLE / INPUT_SUBMITTED for >= REINFORCE_AFTER_MINUTES without an
    ACK. Never re-sends to the same stalled target; the new order is a separate
    differentiated wave that the next tick will dispatch. Returns the child order
    or None.
    """
    order = next((o for o in _orders(root) if o.get("id") == order_id), None)
    # An abandoned order must never be reinforced: spawning a backup wave is
    # re-delivery, which is the one action abandonment exists to rule out.  The
    # state whitelist further down already happens to exclude it, and that is
    # exactly why the guard is here as well -- a guard that only works because a
    # list elsewhere omits a value is one edit away from sending abandoned work.
    if not order or order.get("state") in ("AGENT_ACK", "RETIRED",
                                           ack_protocol.ABANDONED_STATE):
        return None
    if str(order.get("task", "")).startswith("improvement:"):
        # This pipeline has stable stage IDs, leases and explicit pre-input
        # rerouting. Generic reinforcements must not create duplicate waves.
        return None
    candidate = _pick_reinforcement_candidate(order, _orders(root))
    if not candidate:
        return None
    if order.get("state") != "CEASED":
        try:
            age_minutes = (datetime.now(timezone.utc)
                           - datetime.fromisoformat(order["created_at"])).total_seconds() / 60.0
        except (KeyError, ValueError):
            return None
        if age_minutes < REINFORCE_AFTER_MINUTES:
            return None
        if order.get("state") not in ("WINDOW_FOUND", "MESSAGE_VISIBLE", "INPUT_SUBMITTED"):
            return None
    parent_text = order.get("text", "")
    clean_parent = re.sub(r"\[AEP-ACK:[^\]]*\]", "", parent_text).strip()
    child_text = (
        f"[补位] 原题 {order.get('task', '?')} 主订单 {order_id} 暂未达 ACK，"
        f"请采用差异化打法认领并给出可复现证据，不要重复主订单的失败路径。"
        f"主订单上下文见 inbox/{order_id}.json。\n\n{clean_parent}"
    )
    child = submit_order(
        root, target=candidate, task=order.get("task", ""), text=child_text,
        sender=order.get("sender", "commander"),
        priority=order.get("priority", "normal"),
        allow_launch=bool(order.get("allow_launch")),
        transport=order.get("transport", "auto"),
    )
    child["parent"] = order_id
    _atomic(_root(root) / "inbox" / f"{child['id']}.json", child)
    order.setdefault("children", []).append(child["id"])
    _atomic(_state_path(root, order_id), order)
    return child


def tick(root: Path, *, perceive_first: bool = True) -> dict:
    perceived = perceive(root) if perceive_first else {"skipped": True}
    acks = check_acks(root)
    dispatched = []
    for order in _orders(root):
        # ABANDONED is skipped for the same reason AGENT_ACK is: a person has
        # already ended this order's life, so expiry and ceasefire have nothing
        # left to decide.  Without this, an abandoned order that later passed
        # expires_at was rewritten to FAILED/EXPIRED -- the sign-off survived in
        # abandoned_by/abandon_reason but the state stopped saying so, and
        # ack_protocol.abandoned() returned None.  A human's decision silently
        # became an automatic failure.
        if order.get("state") in ("AGENT_ACK", "CEASED", "RETIRED",
                                  ack_protocol.ABANDONED_STATE):
            continue
        try:
            expiry = datetime.fromisoformat(order["expires_at"])
            if datetime.now(timezone.utc) > expiry:
                _record_attempt(root, order, state="FAILED", note="EXPIRED")
                continue
        except (KeyError, ValueError):
            pass
        if _ceased(root, order):
            _record_attempt(root, order, state="CEASED",
                            note=f"ceasefire after {CEASE_AFTER_FAILURES} failed attempts; fix the cause before resuming")
            continue
        # Only a fresh deposit is safe to dispatch. WINDOW_FOUND may have
        # reached the composer before a crash; DEFERRED needs a new preflight.
        if order.get("state") != "DEPOSITED":
            if order.get("state") == "DEFERRED":
                last = (order.get("attempts") or [])[-1] if order.get("attempts") else {}
                receipt = last.get("receipt") or {}
                if receipt.get("wait_for_ready"):
                    readiness = _profiled_readiness(root, order.get("target", ""))
                    if readiness.get("activity_state") == "ready":
                        _record_attempt(root, order, state="DEPOSITED", receipt=readiness,
                                        note="READY_AFTER_BUSY: pre-input resume")
                        fresh = _persisted_order(root, order["id"])
                        dispatched.append({"order": fresh["id"], "target": fresh["target"],
                                           "state": dispatch(root, order=fresh)["state"]})
                elif _auto_resume_due(order):
                    count = _deferred_count(order)
                    _record_attempt(root, order, state="DEPOSITED",
                                    note=f"AUTO_PREINPUT_RESUME: deferral {count} of "
                                         f"{AUTO_RESUME_LIMIT} with nothing typed; retrying the same event id")
                    fresh = _persisted_order(root, order["id"])
                    dispatched.append({"order": fresh["id"], "target": fresh["target"],
                                       "state": dispatch(root, order=fresh)["state"]})
            continue
        dispatched.append({"order": order["id"], "target": order["target"], "state": dispatch(root, order=order)["state"]})
    reinforced = []
    for order in _orders(root):
        if order.get("state") in ("AGENT_ACK", "RETIRED"):
            continue
        child = reinforce(root, order["id"])
        if child:
            reinforced.append({"parent": order["id"], "child": child["id"], "target": child["target"]})
    return {"at": now(), "perceived": perceived, "acks": acks,
            "dispatched": dispatched, "reinforced": reinforced}


def resume_deferred(root: Path, *, order_id: str) -> dict:
    """Explicitly retry only a proven pre-input failure, keeping the event ID."""
    with _dispatch_lock(root, order_id):
        order = _persisted_order(root, order_id)
        if order.get("state") not in {"DEFERRED", "CEASED"} or not _preinput_retryable(order):
            raise ValueError("only DEFERRED or proven pre-input orders can be resumed")
        if datetime.now(timezone.utc) > datetime.fromisoformat(order["expires_at"]):
            return _record_attempt(root, order, state="FAILED", note="EXPIRED")
        if not str(order.get("task", "")).startswith("improvement:"):
            if not _preinput_retryable(order):
                raise ValueError("delivery history is uncertain; cannot resume automatically")
            _record_attempt(root, order, state="DEPOSITED", note="EXPLICIT_PREINPUT_RESUME")
    # Release the non-reentrant order lock before dispatch reacquires it and
    # verifies this exact durable revision; another consumer may have won.
    return dispatch(root, order=order)


def status(root: Path, *, tail: int = 20) -> dict:
    rows = []
    for order in _orders(root)[-tail:]:
        rows.append({key: order.get(key) for key in
                     ("id", "event_id", "target", "task", "priority", "state", "created_at", "updated_at")})
    return {"orders": rows, "states": ORDER_STATES,
            "note": "MESSAGE_VISIBLE is proof we were read, not proof of acceptance. AGENT_ACK needs the target's own token."}


def watch(root: Path, *, interval: float = 30.0, max_ticks: int = 0,
          perceive_first: bool = True, stream: Any = None) -> int:
    """Tick forever, and say so once per tick.

    Two defects were measured here on 2026-11-02, both of which made the daemon
    look healthy while doing nothing:

    ``perceive_first`` was dropped.  ``watch`` called ``tick(root)`` and the
    CLI's ``--no-perceive`` never reached it, so a process asked to skip
    perception ran it anyway -- and perception blocks for minutes once the
    process is elevated and can reach windows a normal one cannot.  Measured: the
    watcher sat inside its first tick for ten minutes, touched no order and wrote
    nothing, while a manual ``tick --no-perceive`` dispatched in under a second.

    The loop printed nothing at all, so an empty log meant both "healthy and
    quiet" and "stuck".  ``stream`` fixes that: one line per tick naming the
    count, the moment and what was dispatched.  A resident process whose health
    cannot be read from its own log is not unattended, it is unobserved.
    """
    count = 0
    while True:
        result = tick(root, perceive_first=perceive_first)
        count += 1
        if stream is not None:
            print(json.dumps({"tick": count, "at": result.get("at"),
                              "dispatched": result.get("dispatched") or [],
                              "perceived": bool(result.get("perceived")
                                                and not result["perceived"].get("skipped"))},
                             ensure_ascii=False), file=stream, flush=True)
        if max_ticks and count >= max_ticks:
            return 0
        time.sleep(max(1.0, interval))
