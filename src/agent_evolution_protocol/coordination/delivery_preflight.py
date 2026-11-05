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

from . import action_verification, input_routing

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


def postflight(receipt: dict) -> dict:
    """Recompute a send receipt.  ADVISORY: commander still owns the live path."""
    checks = action_verification.from_send_report(receipt)["checks"]
    verdict = action_verification.verdict(checks, required=REQUIRED_SEND_CHECKS)
    return {"source": "action_verification", "checks": checks,
            "verified": verdict["verified"], "reason": verdict["reason"],
            "missing": verdict["missing"], "failed": verdict["failed"],
            "declared_task_delivered": bool(receipt.get("task_delivered")),
            "advisory_only": True,
            "note": ("commander still reads the boolean; this recomputation is "
                     "a second opinion and must not be switched in without a "
                     "real-machine measurement")}


def run(root, *, agents=None, send_receipt=None) -> dict:
    root = Path(root)
    if agents is None:
        folder = root / "ui_profiles"
        names = sorted(path.stem for path in folder.glob("*.json")) \
            if folder.is_dir() else []
    else:
        names = list(agents)
    rows = [preflight(root, name) for name in names]
    return {"root": str(root), "scanned": len(rows), "agents": rows,
            "postflight": postflight(send_receipt) if send_receipt is not None else None,
            "models_loaded": 0, "processes_started": 0,
            "ui_input_performed": False, "deployment_performed": False,
            "system_tasks_registered": False, "note": NOTE}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only desktop delivery preflight")
    parser.add_argument("--coord-root", type=Path, default=Path.cwd() / "coordination")
    parser.add_argument("--agent", nargs="*", default=None)
    parser.add_argument("--send-receipt", type=Path, default=None,
                        help="recompute one existing send receipt as an advisory")
    args = parser.parse_args(argv)
    receipt = _read(args.send_receipt) if args.send_receipt else None
    report = run(args.coord_root, agents=args.agent, send_receipt=receipt)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
