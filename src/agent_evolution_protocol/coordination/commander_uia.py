"""Desktop-transport surface for the commander (UIA / OCR / CDP / profiled).

These are the *leaf* delivery and perception helpers: every one of them depends
only on :mod:`commander_base` (for ``_root`` / ``now``) and on the transport
sibling modules.  None of them call :func:`commander.dispatch`, which is what
keeps this module cycle-free -- the recursive dispatch <-> continuation-surface
logic stays in ``commander`` itself.

Function bodies are preserved verbatim from the original ``commander.py``; only
the import header changed.  See ``docs/ARCHITECTURE_REVIEW_20261108.zh-CN.md``.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from agent_evolution_protocol.coordination import client_registry as registry
from agent_evolution_protocol.coordination import session_fingerprint
from agent_evolution_protocol.coordination import transcript_bus as bus
from agent_evolution_protocol.coordination import transport_cdp as cdp
from agent_evolution_protocol.coordination import uia_argv
from agent_evolution_protocol.coordination import ui_fallback
from agent_evolution_protocol.coordination.commander_base import _root, now


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
            value = __import__("json").loads(line)
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
        for key in ("drivable", "blocked_by", "target_elevation", "executor_elevation", "reason",
                    "session_lease", "session_attestation"):
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
        # A process ID says which window happened to be captured, not which
        # conversation produced the reply.  Publishing it as ``session_id``
        # let an ACK from a replaced/restarted window look like an ACK for an
        # order that had used that PID.  Preserve a stable marker when the
        # profile supplies one; an empty value is honestly "unknown" and the
        # ACK/readback layer will hold rather than infer identity.
        try:
            identity_profile = session_fingerprint.merged_profile(
                registry.profile(root, name), session_fingerprint.ui_profile(root, name))
            session_id = str(identity_profile.get("session_marker") or "").strip()
        except (OSError, ValueError, KeyError, TypeError):
            session_id = ""
        published = bus.publish(root, agent=name, text=text, source=source,
                                session_id=session_id,
                                window_title=str(window.get("title", "")))
        results.append({"agent": name, "status": "PUBLISHED", "source": source,
                        "pid": window.get("pid"), "session_id": session_id,
                        "added_line_count": published["added_line_count"]})
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
