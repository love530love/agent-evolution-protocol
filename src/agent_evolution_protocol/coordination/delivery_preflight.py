"""Read-only desktop delivery preflight and post-send second opinion.

Two facts measured on 2026-11-05 motivate this module:

* No component answered, before any input, "can this be sent, by which route,
  and what is missing".  ``desktop-preflight.ps1`` checks registration and
  window count, ``probe_uia.ps1`` answers whether a tree is readable, and the
  ``observe`` receipt has the right fields -- but ``commander._uia_observe``
  is dead code and the live dispatch path goes straight from a text gate to a
  write.  That is why a read-only observation that cannot be interpreted has
  to end in "do not send" rather than a guess.
* ``commander._dispatch`` reads ``task_delivered`` as a boolean.  The
  evidence checks in ``action_verification`` / ``from_send_report`` /
  ``provider_readback`` exist but have no consumer in the live path, which is
  how a side-panel editor once reported ``task_delivered=true``.

This module does NOT change the live path.  It reads files that already exist,
feeds ``input_routing.delivery_verdict`` (the existing decision rule, not a
second one), and recomputes a send receipt with ``action_verification`` as an
ADVISORY second opinion.  Switching commander over to that recomputation
requires a real-machine measurement and is deliberately left for later.

Purely read-only: no external adapter, no input, no focus change, no state
written, no model, no process, no resident loop.  UIPI / integrity-level
checks still belong to the operator before any real send.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import action_verification, dispatch_gate, input_routing, uia_transcript_verdict

NOTE = ("Read-only preflight plus an advisory post-send recomputation. "
        "It never sends, never types, never focuses a window, and does not "
        "change commander's live verdict. UIPI and executor integrity must "
        "still be confirmed before any real delivery.")
REQUIRED_SEND_CHECKS = tuple(
    name for name, _ in action_verification.SEND_REPORT_CHECKS.values())


def _read(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def collect(root, agent: str) -> dict:
    """Read four existing on-disk facts.  Nothing here executes anything."""
    root = Path(root)
    profile_path = root / "ui_profiles" / f"{agent}.json"
    client_path = root / "clients" / f"{agent}.json"
    observe_path = root / "uia_receipts" / f"last-{agent}.json"
    health_path = root / "desktop_health.json"
    return {"profile": _read(profile_path), "client": _read(client_path),
            "observe": _read(observe_path), "health": _read(health_path),
            "sources": {"profile": str(profile_path), "client": str(client_path),
                        "observe": str(observe_path), "health": str(health_path)}}


def gates(facts: dict) -> tuple[list[str], list[str]]:
    """Fail-closed reasons.  A missing check is never treated as a pass."""
    missing: list[str] = []
    warnings: list[str] = []
    profile = facts.get("profile") or {}
    observe = facts.get("observe")
    health = facts.get("health") or {}

    if not profile:
        missing.append("PROFILE_MISSING")
    else:
        if not profile.get("session_marker"):
            warnings.append("SESSION_MARKER_UNSET")
        if profile.get("transport") not in ("uia", "ocr"):
            warnings.append("TRANSPORT_UNKNOWN")
    if not facts.get("client"):
        warnings.append("CLIENT_NOT_REGISTERED")

    if not observe:
        missing.append("OBSERVE_MISSING")
    elif str(observe.get("mode", "")) != "observe":
        missing.append("CAPTURE_ONLY_RECEIPT")
    else:
        marker = str(observe.get("session_marker_check", ""))
        if marker == "transcript-unreadable":
            missing.append("TRANSCRIPT_UNREADABLE")
        elif marker != "seen":
            missing.append("SESSION_MARKER_NOT_SEEN")
        if observe.get("input_found") is not True:
            missing.append("INPUT_NOT_FOUND")
        if not isinstance(observe.get("input_region"), dict):
            missing.append("INPUT_REGION_MISSING")
        activity = str(observe.get("activity_state", "unknown"))
        if activity == "unknown":
            missing.append("ACTIVITY_UNKNOWN")
        elif activity == "busy" and profile.get("require_activity_ready"):
            missing.append("ACTIVITY_BUSY")

    circuit = str(health.get("circuit", "") or "")
    if circuit and circuit != "READY":
        missing.append("WINDOW_ENUMERATION_DEGRADED")
    return missing, warnings


def preflight(root, agent: str) -> dict:
    """One deterministic, read-only answer for one client."""
    facts = collect(root, agent)
    missing, warnings = gates(facts)
    profile = facts.get("profile") or {}
    observe = facts.get("observe") or {}
    transport = str(profile.get("transport", "") or "")
    route = transport if transport in ("uia", "ocr") else "unknown"

    # Reuse the existing decision rule.  A usable observation still gets
    # needs_verification=True because a preflight has never typed anything:
    # the honest answer is "send after verification", never plain "send".
    if missing:
        locate = {"locate_source": None, "reason": missing[0],
                  "needs_verification": True}
    else:
        locate = {"locate_source": "uia" if route == "uia" else "geometry",
                  "reason": "", "needs_verification": True}
    verdict = input_routing.delivery_verdict(locate=locate)

    notes = ["只读预检：不执行外部适配器、不输入、不聚焦窗口；UIPI 与执行器完整性级别另需人工确认。"]
    if route == "ocr":
        notes.append("OCR-only client: no UIA tree, composer is geometry-selected and needs verification.")
    if route == "uia":
        notes.append("UIA transport: the input was observed, not guessed.")
    if profile.get("interject_after_send"):
        notes.append("插话控件在发送后才出现，需轮询等待而不是看一眼。")
    if warnings:
        notes.append("warnings=" + ",".join(warnings))

    return {"agent": agent, "route": route, "readiness": verdict["state"],
            "verdict": verdict, "missing": missing, "warnings": warnings,
            "notes": " ".join(notes),
            "evidence": {
                "sources": facts["sources"],
                "profile_transport": transport,
                "session_marker_check": observe.get("session_marker_check"),
                "input_found": observe.get("input_found"),
                "activity_state": observe.get("activity_state"),
                "health_circuit": (facts.get("health") or {}).get("circuit"),
            },
            "ui_input_performed": False, "advisory_only": True,
            "note": "Preflight only. It does not authorise a delivery."}


def dispatch_text_diagnosis(text: str, *, event_id: str = "") -> dict:
    """Check a *would-be* dispatch body for the two text gates that refused
    real orders on 2026-11-22.  Pure string inspection: no input, no IO.

    ``one_event_id`` is the same rule and the same regex as
    ``dispatch_gate.check`` -- ``EVENT`` is imported, not redefined.  A body
    that carries an auto-minted ``EVT-`` id plus a hand-written ``WAKE-`` id
    is refused before any desktop input, which looks like "nothing happened".
    ``ack_instruction`` checks the same ``AEP-ACK`` token the live gate uses.
    """
    body = str(text or "")
    events = sorted(set(dispatch_gate.EVENT.findall(body)))
    expected = str(event_id or "")
    one_event = len(events) == 1 and (not expected or events[0] == expected)
    markers = sorted(set(dispatch_gate.TASK_MARKER.findall(body)))
    ack = dispatch_gate.ACK_TOKEN in body
    issues = []
    if len(events) != 1:
        issues.append("ONE_EVENT_ID_EXPECTED")
    elif expected and events[0] != expected:
        issues.append("EVENT_ID_MISMATCH")
    if len(markers) > 1:
        issues.append("MULTIPLE_TASK_MARKERS")
    if not ack:
        issues.append("ACK_INSTRUCTION_MISSING")
    return {
        "schema": "aep-dispatch-text-diagnosis-v1",
        "event_ids_found": events, "expected_event_id": expected,
        "task_markers_found": markers, "ack_instruction": ack,
        "one_event_id": one_event, "issues": issues,
        "may_dispatch": not issues,
        "advisory_only": True, "ui_input_performed": False,
        "note": ("Text-only mirror of dispatch_gate's gates. The live gate "
                 "still owns the decision; this answers before any input."),
    }


def session_marker_diagnosis(profile: dict, *, transcript: str = "",
                             observe: dict | None = None) -> dict:
    """Detect the 2026-11-22 qoder bug: ``session_marker`` was a sidebar
    button ('请加入项目协作区') absent from the chat transcript, so the
    original-session gate refused every delivery forever.

    The fix is a profile *configuration* correction, not a relaxation of the
    gate.  This function only names the mismatch; it never changes a verdict
    and never rewrites a profile.  Evidence markers already declared in the
    profile are offered as candidates, so an operator can fix the config
    without guessing.
    """
    profile = profile if isinstance(profile, dict) else {}
    observe = observe if isinstance(observe, dict) else {}
    marker = str(profile.get("session_marker") or "")
    check = str(observe.get("session_marker_check", ""))
    seen = observe.get("session_marker_seen")
    body = transcript or ""
    declared = [str(x) for x in (profile.get("session_evidence_markers") or [])
                if isinstance(x, str) and x.strip()]
    present = [m for m in declared if m in body]
    issues = []
    if not marker:
        issues.append("SESSION_MARKER_UNSET")
    elif body and marker not in body and check not in ("", "seen"):
        # Only name a drift when a transcript (or an explicit observe) proves
        # the configured marker is absent from this conversation.
        issues.append("SESSION_MARKER_NOT_IN_TRANSCRIPT")
    if check == "transcript-unreadable":
        issues.append("TRANSCRIPT_UNREADABLE")
    elif check and check != "seen":
        issues.append("SESSION_MARKER_ABSENT")
    if seen is False and body:
        issues.append("SESSION_MARKER_SEEN_FALSE")
    return {
        "schema": "aep-session-marker-diagnosis-v1",
        "configured_marker": marker, "observe_check": check,
        "observe_seen": seen, "declared_evidence_markers": declared,
        "declared_markers_present_in_transcript": present,
        "suggested_markers": present,
        "issues": issues, "config_drift_suspected": bool(
            "SESSION_MARKER_NOT_IN_TRANSCRIPT" in issues
            or "SESSION_MARKER_ABSENT" in issues),
        "gate_was_relaxed": False, "advisory_only": True,
        "ui_input_performed": False,
        "note": ("Read-only diagnosis. Correct session_marker in the profile "
                 "to a marker visible in the original chat; never loosen the "
                 "seen=True attestation."),
    }


def observe_freshness(root, agent: str, *, max_age_seconds: float = 900.0,
                      now: float | None = None) -> dict:
    """An on-disk observe receipt has no ``at`` field, so its file mtime is
    the only freshness evidence available to a read-only scanner.  The
    2026-11-22 scan found a stale qoder_cn receipt (no window at all) being
    reported as ready.  Fail closed: never call a stale receipt dispatchable.
    """
    import time
    path = Path(root) / "uia_receipts" / f"last-{agent}.json"
    try:
        age = max(0.0, (now if now is not None else time.time()) - path.stat().st_mtime)
    except OSError:
        return {"fresh": False, "age_seconds": None,
                "reason": "OBSERVE_RECEIPT_MISSING",
                "schema": "aep-observe-freshness-v1"}
    fresh = age <= max_age_seconds
    return {"fresh": fresh, "age_seconds": round(age, 3),
            "max_age_seconds": max_age_seconds,
            "reason": "" if fresh else "OBSERVE_STALE",
            "schema": "aep-observe-freshness-v1"}


def dispatch_readiness(root, agent: str, *, text: str = "",
                       event_id: str = "", transcript: str = "") -> dict:
    """One advisory answer: can this agent be dispatched to, and what is
    missing?  Combines the existing preflight with the two 2026-11-22 text /
    marker diagnoses.  It is read-only and never authorises a delivery.
    """
    root = Path(root)
    facts = collect(root, agent)
    pre = preflight(root, agent)
    profile = facts.get("profile") or {}
    observe = facts.get("observe") or {}
    freshness = observe_freshness(root, agent)
    marker = session_marker_diagnosis(
        profile, transcript=transcript, observe=observe)
    text_report = (dispatch_text_diagnosis(text, event_id=event_id)
                   if text else None)
    blockers = list(pre["missing"])
    if marker["config_drift_suspected"]:
        blockers.append("SESSION_MARKER_CONFIG_DRIFT")
    if not profile:
        blockers.append("ORIGINAL_SESSION_PROFILE_REQUIRED")
    if not freshness["fresh"]:
        blockers.append(freshness["reason"])
    if text_report and not text_report["may_dispatch"]:
        blockers.extend(text_report["issues"])
    blockers = sorted(set(blockers))
    if not profile:
        action = "NEED_PROFILE"
    elif marker["config_drift_suspected"]:
        action = "FIX_SESSION_MARKER_CONFIG"
    elif text_report and not text_report["may_dispatch"]:
        action = "FIX_DISPATCH_TEXT"
    elif not freshness["fresh"]:
        action = "REOBSERVE_FIRST"
    elif pre["missing"]:
        action = "WAIT_OR_REOBSERVE"
    else:
        action = "SEND_AFTER_VERIFICATION"
    return {
        "schema": "aep-dispatch-readiness-v1", "agent": agent,
        "action": action, "blockers": blockers,
        "preflight": pre, "session_marker": marker,
        "dispatch_text": text_report, "observe_freshness": freshness,
        "may_dispatch": not blockers and pre["readiness"] == "READY_UNVERIFIED",
        "ui_input_performed": False, "advisory_only": True,
        "note": ("Read-only dispatch readiness. may_dispatch never means "
                 "delivered or ACK; a real send still needs verification."),
    }


def postflight(receipt: dict, *, transcript: str | None = None,
               event_id: str = "", probe: str = "",
               composer_hint: str = "") -> dict:
    """Recompute a send receipt.  ADVISORY: commander still owns the live path.

    ``transcript`` adds a SECOND, independent source read from a read-only UIA
    capture. It sits beside the four OCR checks; the primary verdict, the
    required-check list and ``advisory_only`` are unchanged. Disagreement is
    named instead of resolved, because relaxing the existing contract to make
    a false negative disappear would let an undelivered message pass too.
    """
    checks = action_verification.from_send_report(receipt)["checks"]
    verdict = action_verification.verdict(checks, required=REQUIRED_SEND_CHECKS)
    second = (uia_transcript_verdict.evaluate(
        transcript=transcript or "", event_id=event_id, probe=probe,
        composer_hint=composer_hint) if transcript is not None else None)
    primary = verdict["verified"]
    conflict = bool(second is not None and second["verified"] is not primary)
    if not conflict:
        conflict_note = ""
    elif second["verified"] is True:
        conflict_note = "OCR_BAND_FALSE_NEGATIVE_SUSPECTED"
    else:
        conflict_note = "SECOND_SOURCE_DISAGREES"
    return {"source": "action_verification", "checks": checks,
            "verified": primary, "reason": verdict["reason"],
            "missing": verdict["missing"], "failed": verdict["failed"],
            "declared_task_delivered": bool(receipt.get("task_delivered")),
            "transcript_verdict": second,
            "sources_in_conflict": conflict, "conflict_note": conflict_note,
            "advisory_only": True,
            "note": ("commander still reads the boolean; this recomputation is "
                     "a second opinion and must not be switched in without a "
                     "real-machine measurement")}


def run(root, *, agents=None, send_receipt=None, transcript: str | None = None,
        event_id: str = "", probe: str = "", composer_hint: str = "") -> dict:
    root = Path(root)
    if agents is None:
        folder = root / "ui_profiles"
        names = sorted(path.stem for path in folder.glob("*.json")) \
            if folder.is_dir() else []
    else:
        names = list(agents)
    rows = [preflight(root, name) for name in names]
    return {"root": str(root), "scanned": len(rows), "agents": rows,
            "postflight": (postflight(send_receipt, transcript=transcript,
                                      event_id=event_id, probe=probe,
                                      composer_hint=composer_hint)
                           if send_receipt is not None else None),
            "models_loaded": 0, "processes_started": 0,
            "ui_input_performed": False, "deployment_performed": False,
            "system_tasks_registered": False, "note": NOTE}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only desktop delivery preflight")
    parser.add_argument("--coord-root", type=Path, default=Path.cwd() / "coordination")
    parser.add_argument("--agent", nargs="*", default=None)
    parser.add_argument("--send-receipt", type=Path, default=None,
                        help="recompute one existing send receipt as an advisory")
    parser.add_argument("--transcript-file", type=Path, default=None,
                        help="read-only UIA capture to add a SECOND verdict source")
    parser.add_argument("--event-id", default="",
                        help="event id the transcript must carry above the composer")
    parser.add_argument("--probe", default="",
                        help="optional trailing fragment that must have moved")
    parser.add_argument("--composer-hint", default="",
                        help="exact composer placeholder marking the band boundary")
    parser.add_argument("--text", default="",
                        help="would-be dispatch body to check for one_event_id / ACK / task marker")
    parser.add_argument("--readiness", action="store_true",
                        help="emit a per-agent dispatch-readiness diagnosis (read-only)")
    args = parser.parse_args(argv)
    receipt = _read(args.send_receipt) if args.send_receipt else None
    transcript = None
    if args.transcript_file:
        try:
            transcript = args.transcript_file.read_text(encoding="utf-8")
        except OSError:
            transcript = ""
    if args.readiness:
        names = args.agent or sorted(
            path.stem for path in (Path(args.coord_root) / "ui_profiles").glob("*.json"))
        report = {
            "schema": "aep-dispatch-readiness-scan-v1",
            "agents": [dispatch_readiness(
                args.coord_root, name, text=args.text,
                event_id=args.event_id, transcript=transcript or "")
                for name in names],
            "ui_input_performed": False, "advisory_only": True, "note": NOTE,
        }
    else:
        report = run(args.coord_root, agents=args.agent, send_receipt=receipt,
                     transcript=transcript, event_id=args.event_id,
                     probe=args.probe, composer_hint=args.composer_hint)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
