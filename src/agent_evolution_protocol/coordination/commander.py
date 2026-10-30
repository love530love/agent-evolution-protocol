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
from agent_evolution_protocol.coordination import ui_fallback
from agent_evolution_protocol.coordination import desktop_providers
from agent_evolution_protocol.coordination import desktop_health
from agent_evolution_protocol.coordination import overlay_audit
from agent_evolution_protocol.coordination import dispatch_contract
from agent_evolution_protocol.coordination.overlay_detector import classify
from agent_evolution_protocol.coordination.provider_readback import receipt as make_readback, accepted as readback_accepted
from agent_evolution_protocol.coordination.workspace_identity import validate_workspace_id

SAFE_AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ACK_TOKEN = "AEP-ACK"
CEASE_AFTER_FAILURES = 3
REINFORCE_AFTER_MINUTES = 20  # 主订单停火或久未ACK后，允许候选agent补位

# DEPOSITED -> WINDOW_FOUND -> INPUT_SUBMITTED -> MESSAGE_VISIBLE -> AGENT_ACK
ORDER_STATES = ("DEPOSITED", "DEFERRED", "DISPATCHING", "WINDOW_FOUND", "INPUT_SUBMITTED", "MESSAGE_VISIBLE", "AGENT_ACK", "CEASED", "FAILED", "RETIRED")


def provider_readback(provider: str, event_id: str, raw: dict) -> dict:
    """Normalize adapter receipts; visibility never becomes an ACK."""
    if not isinstance(raw, dict):
        return make_readback(provider=provider, event_id=event_id, state="UNKNOWN", observed=False, error="adapter receipt is not an object")
    if raw.get("agent_accepted") is True:
        state, ack = "AGENT_ACK", True
    elif raw.get("task_delivered") is True:
        state, ack = "MESSAGE_VISIBLE", False
    elif raw.get("status") in {"NO_RECEIPT", "ADAPTER_ERROR", "ADAPTER_MISSING", "PROFILE_DELIVERY_FAILED", "SEND_CONTROL_NOT_FOUND", "send-control-not-found", "input-not-confirmed"}:
        state, ack = "FAILED", False
    else:
        state, ack = "INPUT_SUBMITTED", False
    value = make_readback(provider=provider, event_id=event_id, state=state,
                          observed=bool(raw.get("task_delivered") or raw.get("agent_accepted")),
                          ack=ack, evidence=list(raw.get("evidence", [])) if isinstance(raw.get("evidence", []), list) else [],
                          error=str(raw.get("error", "")))
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
                 workspace_id: str = "") -> dict:
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
    body = text if ACK_TOKEN in text else f"{text.rstrip()}\n\n[{ACK_TOKEN}:{event}]"
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
        "transport": transport, "state": "DEPOSITED", "created_at": created_at,
        "expires_at": expires_at,
        "attempts": [], "ack": None,
        "candidates": _parse_candidates(candidates), "children": [], "parent": None,
        "dispatch": dispatch,
    }
    if event not in body:
        raise ValueError("event id must appear in the message so read-back can verify it")
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
        cmd += ["-ExpectedPid", str(expected_pid), "-AllowUnknownPath"]
    if profile.get("session_label"):
        cmd += ["-SessionLabel", str(profile["session_label"])]
    if profile.get("window_titles"):
        pass
    if ocr:
        cmd += ["-OcrVerify"]
    try:
        done = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace",
                              capture_output=True, timeout=timeout + 90, check=False)
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
        cmd += ["-ExpectedPid", str(expected_pid), "-AllowUnknownPath"]
    if ocr:
        cmd += ["-OcrVerify"]
    try:
        done = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace",
                              capture_output=True, timeout=timeout + 45, check=False)
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
                      allow_launch: bool = False) -> dict:
    profile_path = Path(root) / "ui_profiles" / f"{agent}.json"
    try:
        profile = ui_fallback.load_profile(profile_path)
        if profile["agent"] != agent:
            raise ValueError("UI_PROFILE_AGENT_MISMATCH")
        receipt_path = _root(root) / "receipts" / f"{event_id}-{agent}-profile.json"
        result = ui_fallback.run(
            profile, text, event_id=event_id, receipt_path=receipt_path,
            lock_path=Path(root) / "desktop-input.lock",
            driver=ui_fallback.PowerShellUiaDriver(), allow_launch=allow_launch,
        )
        return {
            "status": result.get("state", "UNKNOWN"),
            "task_delivered": result.get("task_delivered") is True,
            "agent_accepted": result.get("agent_accepted") is True,
            "receipt_file": str(receipt_path),
            "evidence": str(receipt_path),
            "wait_for_ready": result.get("state") in {"AGENT_BUSY", "ACTIVITY_UNCONFIRMED"},
        }
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
        return ui_fallback.PowerShellUiaDriver().observe(profile)
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
    try:
        subprocess.run(cmd, text=True, encoding="utf-8", errors="replace",
                       capture_output=True, timeout=timeout + 90, check=False)
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
        if current.get("state") != "DEPOSITED" or any(
                attempt.get("state") not in {"DEPOSITED", "DEFERRED", "DISPATCHING"}
                for attempt in current.get("attempts", [])):
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
        registry.profile(root, target)
    except ValueError:
        return _record_attempt(root, order, state="DEFERRED",
                               note="UNREGISTERED_CLIENT: register a verified client profile before delivery")
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
    order["session_id"] = str(window["pid"])
    transport, info = _pick_transport(root, target, order.get("transport", "auto"))
    _record_attempt(root, order, state="WINDOW_FOUND", transport=transport, receipt=info)

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
                                    allow_launch=bool(order.get("allow_launch")))
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
    if receipt.get("status") in ("AGENT_BUSY", "ACTIVITY_UNCONFIRMED", "NO_WINDOW",
                                  "LAUNCH_UNVERIFIED", "WINDOW_OR_INPUT_AMBIGUOUS",
                                  "NO_SAFE_COMPOSER", "SESSION_NOT_VERIFIED", "MODEL_NOT_VERIFIED"):
        return _record_attempt(root, order, state="DEFERRED", transport=transport, receipt=receipt,
                               note="PREINPUT_NOT_READY: observe again after the target becomes ready")
    if receipt.get("status") in ("NO_RECEIPT", "ADAPTER_ERROR", "ADAPTER_MISSING", "PROFILE_DELIVERY_FAILED", "SEND_CONTROL_NOT_FOUND", "send-control-not-found", "input-not-confirmed") or receipt.get("returncode", 0) != 0:
        return _record_attempt(root, order, state="CEASED", transport=transport, receipt=receipt,
                               note="DELIVERY_UNPROVEN: adapter failed; manual diagnosis required")
    return _record_attempt(root, order, state="CEASED", transport=transport, receipt=receipt,
                           note="SUBMISSION_UNCONFIRMED: possible send; do not retry automatically")


def check_acks(root: Path) -> dict:
    """Accept only a role-attributed reply event, never a raw screen snapshot."""
    acked = []
    for order in _orders(root):
        if order.get("state") in ("AGENT_ACK", "CEASED", "FAILED", "RETIRED"):
            continue
        token = f"{ACK_TOKEN}:{order['event_id']}"
        for event in bus.read_events(root, agent=order["target"], limit=500):
            if event.get("role") != "assistant" or event.get("direction") != "incoming":
                continue
            if event.get("session_id") != order.get("session_id") or not event.get("session_id"):
                continue
            if event.get("recorded_at", "") <= order.get("created_at", ""):
                continue
            if token not in "\n".join(event.get("added_lines", [])):
                continue
            _record_attempt(root, order, state="AGENT_ACK",
                            receipt={"evidence": token, "event_id": event.get("id")},
                            note="role-attributed agent reply in the anchored session")
            acked.append({"order": order["id"], "target": order["target"], "token": token})
            break
    return {"at": now(), "acked": acked, "checked": len(_orders(root))}


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
    if not order or order.get("state") in ("AGENT_ACK", "RETIRED"):
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
        if order.get("state") in ("AGENT_ACK", "CEASED", "RETIRED"):
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
    """Explicitly retry only a proven pre-input failure, keeping the event ID.

    Never resume CEASED/WINDOW_FOUND/INPUT_SUBMITTED: those may already have
    reached the target. A caller should first resolve the recorded prerequisite.
    """
    with _dispatch_lock(root, order_id):
        order = _persisted_order(root, order_id)
        if order.get("state") != "DEFERRED":
            raise ValueError("only DEFERRED pre-input orders can be resumed")
        if datetime.now(timezone.utc) > datetime.fromisoformat(order["expires_at"]):
            return _record_attempt(root, order, state="FAILED", note="EXPIRED")
        if not str(order.get("task", "")).startswith("improvement:"):
            if any(attempt.get("state") not in {"DEPOSITED", "DEFERRED", "DISPATCHING"}
                   for attempt in order.get("attempts", [])):
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


def watch(root: Path, *, interval: float = 30.0, max_ticks: int = 0) -> int:
    count = 0
    while True:
        tick(root)
        count += 1
        if max_ticks and count >= max_ticks:
            return 0
        time.sleep(max(1.0, interval))
