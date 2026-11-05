"""Recomputable verdict for one evidence-backed agent handoff loop.

Before this module, "only VERIFIED closes a task" was a documented rule with
no function in ``src`` able to answer the question.  The only producer of the
literal ``RESULT_VERIFIED`` was ``dual_channel.WakeState``, and it required the
caller to pass ``result_verified=True`` itself.

This module does **not** invent a new truth.  It recomputes the three-way
evidence already required by
``docs/coordination/EVIDENCE_BACKED_AGENT_HANDOFF.zh-CN.md``:

1. ``delegation/receipts/<event_id>.json`` is ``AGENT_ACK`` with
   ``agent_accepted=true`` and the expected target;
2. ``delegation/artifacts/<event_id>/<target>.json`` carries an analysis whose
   SHA-256 is **recomputed from the stored text**;
3. the sender's ``<event_id>-reply.json`` carries that same digest, and so does
   the receipt reply.

Anything short of all three is ``NOT_VERIFIED`` with a named reason.  Read-only,
socket-free, model-free, no process, no state written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SCHEMA = "aep-loop-verdict-v1"
VERIFIED = "VERIFIED"
NOT_VERIFIED = "NOT_VERIFIED"
NOTE = ("Recomputed three-way evidence only. Not a desktop-send proof, not a "
        "model judgement, and never a substitute for an agent's own ACK.")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _report(event_id: str, reasons: list[str], checks: dict, digest: str) -> dict:
    return {"schema": SCHEMA, "event_id": event_id,
            "verdict": VERIFIED if not reasons else NOT_VERIFIED,
            "reasons": reasons, "checks": checks,
            "evidence_sha256": digest, "note": NOTE}


def check(root, bridge_root, event_id: str, *, sender: str = "",
          target: str = "") -> dict:
    """Answer ``VERIFIED`` / ``NOT_VERIFIED`` from files, never from a claim."""
    root, bridge_root = Path(root), Path(bridge_root)
    reasons: list[str] = []
    checks = {"request": False, "receipt": False, "artifact": False,
              "digest_recomputed": False, "reply": False, "hash_agreement": False}
    digest = ""

    request = _read(root / "delegation" / "inbox" / f"{event_id}.json")
    if not request:
        reasons.append("REQUEST_MISSING")
        return _report(event_id, reasons, checks, digest)
    checks["request"] = True
    s = sender or str(request.get("from", ""))
    t = target or str(request.get("to", ""))
    if sender and request.get("from") != sender:
        reasons.append("SENDER_MISMATCH")
    if target and request.get("to") != target:
        reasons.append("TARGET_MISMATCH")

    receipt = _read(root / "delegation" / "receipts" / f"{event_id}.json")
    if not receipt:
        reasons.append("RECEIPT_MISSING")
    elif receipt.get("state") != "AGENT_ACK" or not receipt.get("agent_accepted"):
        reasons.append("RECEIPT_NOT_ACK")
    elif receipt.get("to") != t:
        reasons.append("TARGET_MISMATCH")
    else:
        checks["receipt"] = True

    artifact = _read(root / "delegation" / "artifacts" / event_id / f"{t}.json")
    if not artifact:
        reasons.append("ARTIFACT_MISSING")
    else:
        digest = str(artifact.get("sha256", ""))
        recomputed = sha256_text(str(artifact.get("analysis", "")).strip())
        if not digest or digest != recomputed:
            reasons.append("ARTIFACT_DIGEST_MISMATCH")
        else:
            checks["artifact"] = True
            checks["digest_recomputed"] = True
        if digest and digest not in str((receipt or {}).get("reply", "")):
            reasons.append("RECEIPT_HASH_MISMATCH")

    reply = _read(Path(bridge_root) / "messages" / s / f"{event_id}-reply.json")
    if not reply:
        reasons.append("REPLY_MISSING")
    elif not digest or digest not in str(reply.get("text", "")):
        reasons.append("REPLY_HASH_MISMATCH")
    elif checks["digest_recomputed"]:
        checks["reply"] = True
        checks["hash_agreement"] = True

    return _report(event_id, reasons, checks, digest)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Recompute one handoff loop verdict")
    parser.add_argument("--coord-root", type=Path, default=Path.cwd() / "coordination")
    parser.add_argument("--bridge-root", type=Path, required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--sender", default="")
    parser.add_argument("--target", default="")
    args = parser.parse_args(argv)
    print(json.dumps(check(args.coord_root, args.bridge_root, args.event_id,
                           sender=args.sender, target=args.target),
                     ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
