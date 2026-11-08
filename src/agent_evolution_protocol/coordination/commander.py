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


def _root(base: Path) -> Path:
    return Path(base) / "commander"


def _atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        validate_workspace_id(workspace_id)
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


def _pwsh() -> str:
    executable = shutil.which("pwsh.exe")
    if not executable:
        raise RuntimeError("PowerShell 7 (pwsh.exe) is required for desktop adapters")
    return executable


def _uia_deliver(root: Path, agent: str, text: str, event_id: str, *,
                 timeout: int = 20, ocr: bool = True, expected_pid: int = 0) -> dict:
    adapter = Path(__file__).resolve().parent / "wake_adapters" / "uia_session.ps1"
    if not adapter.is_file():
        return {"status": "ADAPTER_MISSING", "task_delivered": False}
    profile = {}
    try:
        profile = registry.profile(root, agent)
    except ValueError:
        pass
    out_file = str(_root(root) / "captures" / f"{agent}-{event_id}.txt")
    receipt_file = str(_root(root) / "receipts" / f"{event_id}-{agent}.json")
    cmd = [_pwsh(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(adapter),
           "-Agent", agent, "-ProcessName", profile.get("process") or agent,
           "-Mode", "deliver", "-OutFile", out_file, "-ReceiptFile", receipt_file,
           "-EventId", event_id, "-Text", text, "-ReadBackTimeoutSeconds", str(timeout)]
    if profile.get("exe"):
        cmd += ["-ExpectedPath", profile["exe"]]
    if expected_pid:
        # Elevated Electron clients may hide Process.Path from our token.
        # The exact PID was selected from a unique/anchored visible window;
        # permit unknown path only with that PID constraint in place.
        # -AllowUnknownPath itself is added by the merge below, so it is named
        # exactly once.
        cmd += ["-ExpectedPid", str(expected_pid)]
    if profile.get("session_label"):
        cmd += ["-SessionLabel", str(profile["session_label"])]
    if ocr:
        cmd += ["-OcrVerify"]
    # Same one copy the operator command uses: OCR language, session marker,
    # send mode, composer hints, clipboard opt-in.  This path used to pass
    # -OcrVerify without -OcrLanguage, so a Chinese UI was read by the en-US
    # recognizer and no marker ever matched.
    effective = dict(profile)
    if expected_pid:
        effective["allow_unknown_path"] = True
    cmd = ui_fallback.merge_profile_args(cmd, effective)
    try:
        done = uia_argv.run(cmd, timeout=timeout + 90)
    except Exception as exc:  # noqa: BLE001
        return {"status": "ADAPTER_ERROR", "error": str(exc), "task_delivered": False}
    report = None
    for line in reversed(done.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            report = candidate
            break
    return report or {"status": "NO_RECEIPT", "returncode": done.returncode,
                      "stderr": done.stderr[-800:], "task_delivered": False}


def _uia_observe(root: Path, agent: str, *, expected_pid: int = 0,
                 ocr: bool = True, timeout: int = 20) -> dict:
    """Read-only readiness probe used immediately before any desktop input.

    This is deliberately separate from ``_uia_deliver``.  A visible composer
    can coexist with an active tool run; sending at that point can interrupt or
    queue work in the wrong turn.  The probe therefore has to report a stable
    ready state before the input lease is consumed.
    """
    adapter = Path(__file__).resolve().parent / "wake_adapters" / "uia_session.ps1"
    if not adapter.is_file():
        return {"status": "ADAPTER_MISSING", "activity_state": "unknown",
                "task_delivered": False}
    profile = {}
    try:
        profile = registry.profile(root, agent)
    except ValueError:
        pass
    cmd = [_pwsh(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(adapter),
           "-Agent", agent, "-ProcessName", profile.get("process") or agent,
           "-Mode", "observe"]
    if profile.get("title"):
        cmd += ["-ExpectedTitle", profile["title"]]
    if profile.get("exe"):
        cmd += ["-ExpectedPath", profile["exe"]]
    if expected_pid:
        cmd += ["-ExpectedPid", str(expected_pid)]
    if ocr:
        cmd += ["-OcrVerify"]
    effective = dict(profile)
    if expected_pid:
        effective["allow_unknown_path"] = True
    cmd = ui_fallback.merge_profile_args(cmd, effective)
    try:
        done = uia_argv.run(cmd, timeout=timeout + 45)
    except Exception as exc:  # noqa: BLE001
        return {"status": "ADAPTER_ERROR", "activity_state": "unknown",
                "error": str(exc), "task_delivered": False}
    for line in reversed(done.stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {"status": "NO_RECEIPT", "activity_state": "unknown",
            "returncode": done.returncode, "stderr": done.stderr[-800:],
            "task_delivered": False}


def _profiled_deliver(root: Path, agent: str, text: str, event_id: str, *,
                      allow_launch: bool = False, urgent_interject: bool = False,
                      workspace_id: str = "") -> dict:
    profile_path = Path(root) / "ui_profiles" / f"{agent}.json"
    try:
        profile = ui_fallback.load_profile(profile_path)
        if profile["agent"] != agent:
            raise ValueError("UI_PROFILE_AGENT_MISMATCH")
        receipt_path = _root(root) / "receipts" / f"{event_id}-{agent}-profile.json"
        from . import uia3
        result = ui_fallback.run(
            profile, text, event_id=event_id, receipt_path=receipt_path,
            lock_path=Path(root) / "desktop-input.lock",
            driver=uia3.driver_for(profile), allow_launch=allow_launch,
            urgent=urgent_interject,
            session_root=Path(root),
            # A legacy order may not yet carry a logical workspace ID.  It is
            # still fenced inside this coordination root; new cross-route
            # callers should pass the same explicit ID to every adapter.
            workspace_id=workspace_id or str(Path(root).resolve()),
        )
        receipt = {
            "status": result.get("state", "UNKNOWN"),
            "task_delivered": result.get("task_delivered") is True,
            "agent_accepted": result.get("agent_accepted") is True,
            "receipt_file": str(receipt_path),
            "evidence": str(receipt_path),
            "wait_for_ready": result.get("state") in {"AGENT_BUSY", "ACTIVITY_UNCONFIRMED"},
        }
        # Carry the integrity verdict into the durable order history.  Without
        # it the receipt says "no input" and leaves the reader to work out that
        # the reason was UIPI rather than a client that would not focus.
        for key in ("drivable", "blocked_by", "target_elevation", "executor_elevation", "reason", "session_lease"):
            if result.get(key) not in (None, ""):
                receipt[key] = result[key]
        return receipt
    except Exception as exc:  # noqa: BLE001 - profile failures must never fall through to blind UI input
        return {"status": "PROFILE_DELIVERY_FAILED", "task_delivered": False,
                "error": str(exc)[:800]}


def _profiled_readiness(root: Path, agent: str) -> dict:
    """Read-only profile probe used to resume a busy desktop Agent.

    It never selects a session, focuses a window, writes the clipboard or
    clicks.  A supervisor may call it at its normal low-frequency tick without
    perturbing the user's desktop.
    """
    profile_path = Path(root) / "ui_profiles" / f"{agent}.json"
    if not profile_path.is_file():
        return {"status": "NO_PROFILE", "activity_state": "unknown"}
    try:
        profile = ui_fallback.load_profile(profile_path)
        from . import uia3
        return uia3.driver_for(profile).observe(profile)
    except Exception as exc:  # noqa: BLE001 - readiness is advisory and read-only
        return {"status": "READINESS_PROBE_FAILED", "activity_state": "unknown",
                "error": str(exc)[:800]}


def _capture_uia(root: Path, agent: str, *, timeout: int = 20) -> tuple[str, str]:
    adapter = Path(__file__).resolve().parent / "wake_adapters" / "uia_session.ps1"
    if not adapter.is_file():
        return "", "uia"
    profile = {}
    try:
        profile = registry.profile(root, agent)
    except ValueError:
        pass
    out_file = str(_root(root) / "captures" / f"{agent}-poll.txt")
    cmd = [_pwsh(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(adapter),
           "-Agent", agent, "-ProcessName", profile.get("process") or agent,
           "-Mode", "capture", "-OutFile", out_file, "-OcrVerify"]
    if profile.get("exe"):
        cmd += ["-ExpectedPath", profile["exe"]]
    # Perception had the same missing -OcrLanguage as dispatch: the capture was
    # taken with the en-US recognizer on a Chinese UI, so the text it published
    # to the bus was Latin look-alikes and every marker lookup failed.
    cmd = ui_fallback.merge_profile_args(cmd, profile)
    try:
        uia_argv.run(cmd, timeout=timeout + 90)
    except Exception:  # noqa: BLE001
        return "", "uia"
    try:
        return Path(out_file).read_text(encoding="utf-8", errors="ignore"), "screen-ocr"
    except OSError:
        return "", "uia"


def perceive(root: Path, *, agents: list[str] | None = None) -> dict:
    """Read every reachable client window into the shared bus."""
    discovery = registry.discover(root)
    results = []
    for client in discovery["clients"]:
        name = client["agent"]
        if agents and name not in agents:
            continue
        if client.get("out_of_scope"):
            # Perception is read-only, but it is not harmless: publishing a
            # browser tab into the shared bus copies an unrelated human
            # conversation into every agent's context.  Refuse at the source.
            results.append({"agent": name, "status": "OUT_OF_SCOPE",
                            "published": False, "reason": client["out_of_scope"]})
            continue
        if not client["windows"]:
            results.append({"agent": name, "status": "NO_WINDOW", "published": False})
            continue
        if not client["unique"] and not client["anchored"]:
            results.append({"agent": name, "status": "WINDOW_NOT_UNIQUE",
                            "windows": len(client["windows"]), "published": False,
                            "hint": "set an anchor pid with coord-client anchor to disambiguate"})
            continue
        window = next((w for w in client["windows"] if w.get("is_anchor")), client["windows"][0])
        text, source = _capture_uia(root, name)
        if not text.strip():
            results.append({"agent": name, "status": "NOTHING_READABLE", "published": False})
            continue
        published = bus.publish(root, agent=name, text=text, source=source,
                                session_id=str(window.get("pid", "")),
                                window_title=str(window.get("title", "")))
        results.append({"agent": name, "status": "PUBLISHED", "source": source,
                        "pid": window.get("pid"), "added_line_count": published["added_line_count"]})
    return {"at": now(), "perceived": results,
            "note": "Perception is read-only. NOTHING_READABLE is a transport gap, not idleness."}


def _pick_transport(root: Path, agent: str, preferred: str = "auto") -> tuple[str, dict]:
    """Use CDP only when explicitly pinned to this client's anchored session."""
    if preferred in ("auto", "cdp"):
        profile = registry.profile(root, agent)
        anchor = registry.load_anchor(root, agent)
        port = profile.get("cdp_port")
        target_id = profile.get("cdp_target_id")
        if (port and target_id and anchor.get("pid")
                and _cdp_port_owned_by_anchor(int(port), int(anchor["pid"]))
                and cdp.probe(int(port), timeout=0.4).get("open")):
            target = next((row for row in cdp.targets(int(port)) if row.get("id") == target_id), None)
            if target and target.get("ws"):
                return "cdp", {"port": int(port), "target": target, "anchor_pid": anchor["pid"]}
    return "uia", {}


def _cdp_port_owned_by_anchor(port: int, anchor_pid: int) -> bool:
    """Fail closed unless the listening process is the anchor or its child."""
    try:
        import psutil  # optional: lack of process attribution disables CDP

        for connection in psutil.net_connections(kind="tcp"):
            if not connection.laddr or connection.laddr.port != port:
                continue
            if connection.status != psutil.CONN_LISTEN or not connection.pid:
                continue
            process = psutil.Process(connection.pid)
            anchor = psutil.Process(anchor_pid)
            if (process.pid == anchor_pid
                    or any(parent.pid == anchor_pid for parent in process.parents())
                    or any(parent.pid == process.pid for parent in anchor.parents())):
                return True
    except Exception:  # noqa: BLE001 - unavailable process attribution must fail closed
        return False
    return False


def _continuation_surfaces(root: Path, order: dict, *, blocked_target: str) -> list[dict]:
    """Other clients where this same conversation is open and can be driven.

    The question ``continuity`` answers is "same conversation, different
    surface", and it is the only question that makes an automatic switch safe:
    the digest covers agent/exe/title, so the same conversation in another
    client hashes differently and looks like a stranger.  ``continues is True``
    means the conversation marker matches; ``None`` means there is nothing to
    compare and is treated as a refusal, because two unlabelled threads are
    indistinguishable and guessing is how a message lands in the wrong one.

    Out-of-scope surfaces are never candidates, and neither is the blocked
    target itself.
    """
    recorded = session_fingerprint.read(root, order.get("target", ""))
    if not recorded:
        return []
    try:
        discovery = registry.discover(root)
    except Exception:  # noqa: BLE001 - a degraded enumeration is not a candidate list
        return []
    candidates = []
    for row in discovery.get("clients", []):
        name = row.get("agent")
        if not name or name == blocked_target or row.get("out_of_scope"):
            continue
        windows = row.get("windows") or []
        if not windows:
            continue
        if not row.get("unique") and not row.get("anchored"):
            continue
        window = next((item for item in windows if item.get("is_anchor")), windows[0])
        try:
            profile_row = session_fingerprint.merged_profile(
                registry.profile(root, name), session_fingerprint.ui_profile(root, name))
            observed = session_fingerprint.observe(agent=name, window=window,
                                                   profile=profile_row)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        verdict = session_fingerprint.continuity(recorded, observed)
        if verdict.get("continues") is True:
            candidates.append({"agent": name, "pid": window.get("pid"),
                               "window_title": window.get("title", ""),
                               "continuity": verdict, "observed": observed})
    return candidates


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
            detail = json.loads(Path(str(receipt_file)).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
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
    profile_row = session_fingerprint.merged_profile(
        profile_row, session_fingerprint.ui_profile(root, target))
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
    # Last gate before anything is typed.  It runs here, not inside a transport,
    # because "which text goes where" is a property of the order, and a check
    # implemented per transport is a check that will be missing from the next
    # transport someone adds.
    refused = _gate_refusal(root, order, profile_row=profile_row, window=window)
    if refused is not None:
        return refused
    order["session_id"] = str(window["pid"])
    transport, info = _pick_transport(root, target, order.get("transport", "auto"))
    _record_attempt(root, order, state="WINDOW_FOUND", transport=transport,
                    receipt={**info, "session_fingerprint": identity})

    if transport == "cdp":
        try:
            receipt = cdp.deliver(info["target"]["ws"], order["text"], event_id=order["event_id"], wait_seconds=8)
        except cdp.CdpError as exc:
            return _record_attempt(root, order, state="FAILED", transport=transport, note=f"CDP_ERROR {exc}")
        if receipt.get("task_delivered"):
            return _record_attempt(root, order, state="MESSAGE_VISIBLE", transport=transport, receipt=receipt)
        return _record_attempt(root, order, state="INPUT_SUBMITTED", transport=transport, receipt=receipt,
                               note="typed into the composer but the event id never appeared; not a delivery")

    profile_path = Path(root) / "ui_profiles" / f"{target}.json"
    if profile_path.is_file():
        receipt = _profiled_deliver(root, target, order["text"], order["event_id"],
                                    allow_launch=bool(order.get("allow_launch")),
                                    urgent_interject=bool(order.get("urgent_interject")),
                                    workspace_id=str(order.get("workspace_id", "")))
        transport = "profiled-ui"
    else:
        receipt = _uia_deliver(root, target, order["text"], order["event_id"],
                               expected_pid=int(window["pid"]))
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


def _parse_candidates(raw: object) -> list[str]:
    """Split a comma/space separated candidate string into safe agent names."""
    if not raw:
        return []
    items = [a.strip().lower() for a in re.split(r"[,\s]+", str(raw)) if a.strip()]
    return [a for a in items if SAFE_AGENT.fullmatch(a)]


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
