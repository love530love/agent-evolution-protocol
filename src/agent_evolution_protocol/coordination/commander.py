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

SAFE_AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ACK_TOKEN = "AEP-ACK"
CEASE_AFTER_FAILURES = 3

# DEPOSITED -> WINDOW_FOUND -> INPUT_SUBMITTED -> MESSAGE_VISIBLE -> AGENT_ACK
ORDER_STATES = ("DEPOSITED", "WINDOW_FOUND", "INPUT_SUBMITTED", "MESSAGE_VISIBLE", "AGENT_ACK", "CEASED", "FAILED")


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
                 transport: str = "auto", expires_minutes: int = 120) -> dict:
    """Queue a directive for one client. Nothing is sent until a tick runs."""
    if not SAFE_AGENT.fullmatch(target) or not SAFE_AGENT.fullmatch(sender):
        raise ValueError("invalid agent name")
    if not text.strip():
        raise ValueError("text is required")
    order_id = now().replace(":", "").replace(".", "") + "-" + uuid4().hex[:8]
    event = event_id or ("EVT-" + uuid4().hex[:10].upper())
    body = text if ACK_TOKEN in text else f"{text.rstrip()}\n\n[{ACK_TOKEN}:{event}]"
    payload = {
        "id": order_id, "event_id": event, "target": target, "sender": sender,
        "task": task, "priority": priority, "text": body, "allow_launch": allow_launch,
        "transport": transport, "state": "DEPOSITED", "created_at": now(),
        "expires_at": datetime.fromtimestamp(time.time() + expires_minutes * 60, timezone.utc).isoformat(),
        "attempts": [], "ack": None,
    }
    if event not in body:
        raise ValueError("event id must appear in the message so read-back can verify it")
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
    attempt = {"at": now(), "state": state, "transport": transport,
               "receipt": receipt or {}, "note": note}
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
    if profile.get("window_titles"):
        pass
    if ocr:
        cmd += ["-OcrVerify"]
    try:
        done = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout + 90, check=False)
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
        }
    except Exception as exc:  # noqa: BLE001 - profile failures must never fall through to blind UI input
        return {"status": "PROFILE_DELIVERY_FAILED", "task_delivered": False,
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
        subprocess.run(cmd, text=True, capture_output=True, timeout=timeout + 90, check=False)
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


def dispatch(root: Path, *, order: dict) -> dict:
    target = order["target"]
    client = None
    for row in registry.discover(root, target)["clients"]:
        if row["agent"] == target:
            client = row
            break
    windows = (client or {}).get("windows", [])
    if not windows:
        if not order.get("allow_launch"):
            return _record_attempt(root, order, state="FAILED", note="NO_WINDOW: client is not open and launch is not authorised")
        launched = registry.launch(root, target, allow=True)
        if not launched.get("launched"):
            return _record_attempt(root, order, state="FAILED", receipt=launched, note="LAUNCH_REFUSED")
        _record_attempt(root, order, state="DEPOSITED", note=f"launched pid={launched.get('pid')}")
        return order

    if not client["unique"] and not client["anchored"]:
        return _record_attempt(root, order, state="FAILED",
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
    if receipt.get("status") in ("NO_RECEIPT", "ADAPTER_ERROR", "ADAPTER_MISSING", "PROFILE_DELIVERY_FAILED") or receipt.get("returncode", 0) != 0:
        return _record_attempt(root, order, state="CEASED", transport=transport, receipt=receipt,
                               note="DELIVERY_UNPROVEN: adapter failed; manual diagnosis required")
    return _record_attempt(root, order, state="CEASED", transport=transport, receipt=receipt,
                           note="SUBMISSION_UNCONFIRMED: possible send; do not retry automatically")


def check_acks(root: Path) -> dict:
    """Accept only a role-attributed reply event, never a raw screen snapshot."""
    acked = []
    for order in _orders(root):
        if order.get("state") in ("AGENT_ACK", "CEASED"):
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


def tick(root: Path, *, perceive_first: bool = True) -> dict:
    perceived = perceive(root) if perceive_first else {"skipped": True}
    acks = check_acks(root)
    dispatched = []
    for order in _orders(root):
        if order.get("state") in ("AGENT_ACK", "CEASED"):
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
        if order.get("state") in ("MESSAGE_VISIBLE", "INPUT_SUBMITTED"):
            continue
        dispatched.append({"order": order["id"], "target": order["target"], "state": dispatch(root, order=order)["state"]})
    return {"at": now(), "perceived": perceived, "acks": acks, "dispatched": dispatched}


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
