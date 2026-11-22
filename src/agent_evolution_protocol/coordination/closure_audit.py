"""Read-only, three-layer closure audit. Never promotes one layer into another.

A relay leg can be ``VERIFIED`` on filesystem evidence while its message was
never delivered to a desktop and while no competition result was ever reported.
Those are three different facts with three different producers:

  1. ``evidence``  -- loop_verdict: receipt + artifact + hash agreement.
  2. ``delivery``  -- delivery_audit: mailbox -> window -> visible -> ACK.
  3. ``reporting`` -- campaign returns: submission_id + per-chip status.

This module computes all three independently and gives each its own state and
``next_required``.  It performs no writes, no subprocesses, no network, no
model calls and no desktop actions.  An ``identity_only`` agent is marked as
mailbox-only by design instead of being reported as a failed desktop delivery.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import client_registry, delivery_audit, loop_verdict, relay_drill

__all__ = ["audit", "main"]

SCHEMA = "aep-closure-audit-v1"
NOTE = (
    "Three independent layers. EVIDENCE_VERIFIED never implies desktop delivery "
    "or an official result report. Read-only recomputation from files."
)


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _legs(root: Path) -> list[dict]:
    payload = _read_json(Path(root) / relay_drill.PLAN_NAME)
    if not payload:
        return []
    legs = payload.get("legs")
    return [row for row in legs if isinstance(row, dict)] if isinstance(legs, list) else []


def _identity_only(agent: str) -> bool:
    profile = client_registry.BUILTIN.get(agent, {})
    return bool(profile.get("identity_only"))


def _evidence_layer(root: Path, bridge_root: Path, legs: list[dict]) -> dict:
    rows = []
    for leg in legs:
        event_id = str(leg.get("event_id", ""))
        if not event_id:
            continue
        result = loop_verdict.check(
            root, bridge_root, event_id,
            sender=str(leg.get("sender", "")), target=str(leg.get("target", "")))
        rows.append({"event_id": event_id, "sender": leg.get("sender", ""),
                     "target": leg.get("target", ""),
                     "verdict": result["verdict"], "reasons": result["reasons"]})
    verified = sum(row["verdict"] == loop_verdict.VERIFIED for row in rows)
    return {"state": "CLOSED" if rows and verified == len(rows) else "OPEN",
            "verified": verified, "total": len(rows), "legs": rows,
            "next_required": "" if rows and verified == len(rows) else
            "AGENT_ACK_WITH_ARTIFACT_AND_HASH"}


def _delivery_layer(root: Path, legs: list[dict]) -> dict:
    audit_rows = delivery_audit.audit(root).get("rows", [])
    by_event = {str(row.get("id", "")): row for row in audit_rows}
    rows = []
    for leg in legs:
        event_id = str(leg.get("event_id", ""))
        target = str(leg.get("target", ""))
        if not event_id:
            continue
        row = by_event.get(event_id, {})
        identity_only = _identity_only(target)
        desktop = str(row.get("desktop", "")) or "NOT_ATTEMPTED"
        if identity_only:
            state = "MAILBOX_ONLY_BY_DESIGN"
            next_required = "SELF_REPORT_READ_AND_EVIDENCE_BACKED_ACK"
        elif row.get("agent_accepted") and desktop == "AGENT_ACK":
            state = "DESKTOP_ACK"
            next_required = ""
        else:
            state = "NOT_DELIVERED"
            next_required = str(row.get("next_required", "CLIENT_BOUND"))
        rows.append({"event_id": event_id, "target": target,
                     "identity_only": identity_only, "mailbox": row.get("mailbox", "DEPOSITED"),
                     "desktop": desktop, "state": state,
                     "next_required": next_required})
    accepted = sum(row["state"] in {"DESKTOP_ACK", "MAILBOX_ONLY_BY_DESIGN"}
                   for row in rows)
    return {"state": "CLOSED" if rows and accepted == len(rows) else "OPEN",
            "delivered": accepted, "total": len(rows), "messages": rows,
            "next_required": "" if rows and accepted == len(rows) else
            "REAL_DESKTOP_DELIVERY"}


def _reporting_layer(root: Path) -> dict:
    """Only an *officially verified* return closes the reporting layer.

    A locally supplied snapshot is not proof of an official submission.  A row
    with ``verified_official=False``, an empty submission_id or a malformed
    package hash must never read as "the competition result was delivered".
    This was found by the expert-robust audit (P1 false positive).
    """
    import re
    sha64 = re.compile(r"^[0-9a-f]{64}$")
    returns = []
    for path in sorted((root / "campaign" / "missions").glob("*.json")):
        state = _read_json(path)
        if not state:
            continue
        for row in state.get("returns", []):
            if not isinstance(row, dict):
                continue
            sid = str(row.get("submission_id", "")).strip()
            digest = str(row.get("package_sha256", "")).strip().lower()
            verified = bool(row.get("verified_official"))
            officially_verified = bool(verified and sid and sha64.fullmatch(digest))
            returns.append({"task": state.get("task", path.stem),
                            "submission_id": sid,
                            "package_sha256": digest,
                            "verified_official": verified,
                            "officially_verified": officially_verified})
    closed = any(row["officially_verified"] for row in returns)
    return {"state": "CLOSED" if closed else "OPEN",
            "returns": len(returns),
            "officially_verified": sum(row["officially_verified"] for row in returns),
            "submissions": returns,
            "next_required": "" if closed else
            "OFFICIAL_VERIFIED_RETURN_WITH_SUBMISSION_ID_AND_SHA256"}


def audit(root: Path, bridge_root: Path) -> dict:
    """Recompute the three closure layers independently, read-only."""
    root, bridge_root = Path(root), Path(bridge_root)
    legs = _legs(root)
    layers = {"evidence": _evidence_layer(root, bridge_root, legs),
              "delivery": _delivery_layer(root, legs),
              "reporting": _reporting_layer(root)}
    closed = [name for name, row in layers.items() if row["state"] == "CLOSED"]
    return {"schema": SCHEMA, "coord_root": str(root), "layers": layers,
            "layers_closed": closed,
            "all_closed": len(closed) == len(layers),
            "note": NOTE}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd() / "coordination",
                        help="coordination directory (read-only)")
    parser.add_argument("--bridge-root", type=Path, required=True,
                        help="bridge directory holding reply files")
    args = parser.parse_args(argv)
    print(json.dumps(audit(args.root, args.bridge_root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
