"""Handoff packets: a bounded, verifiable transfer of one workspace.

A takeover is not "the new agent reads the chat". It is a packet that names the
workspace, the frozen anchor, the snapshot it refers to, why the transfer
happened, and which capability the receiver must have. The receiver can only
accept if it is the named agent and holds the capability.
"""
from __future__ import annotations

import re
from uuid import uuid4

from ..coordination.workspace_identity import validate_workspace_id
from .snapshot import verify as verify_snapshot
from .store import digest, stamp


HANDOFF_SCHEMA = "aep-handoff-packet-v1"
REASONS = frozenset({"token_exhausted", "stalled", "refusal", "model_switch",
                     "manual_switch", "resource_blocked", "conflict_review"})
CAPABILITY = re.compile(r"[a-z][a-z0-9-]{1,31}")
_HEX = re.compile(r"[0-9a-f]{64}")


def snapshot_ref(snapshot):
    """A resolvable pointer: which snapshot file, pinned to which digest."""
    return f"snapshots/v{int(snapshot['version']):04d}.json#{snapshot['digest']}"


def packet(snapshot, *, from_agent, to_agent, reason, next_action, moment=None,
           evidence=(), reply_due_at=None):
    """Build a packet that points at a snapshot instead of repeating it."""
    verify_snapshot(snapshot)
    for label, value in (("from_agent", from_agent), ("to_agent", to_agent)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} is required")
    if from_agent.strip() == to_agent.strip():
        raise ValueError("handoff target must differ from the current agent")
    if reason not in REASONS:
        raise ValueError(f"reason must be one of {sorted(REASONS)}")
    if not isinstance(next_action, str) or not next_action.strip():
        raise ValueError("next_action is required")
    handoff_id = "H-" + uuid4().hex[:16]
    anchor_sha = snapshot["anchored_request"]["sha256"]
    body = {
        "schema": HANDOFF_SCHEMA,
        "event_id": handoff_id,
        "handoff_id": handoff_id,
        "workspace_id": snapshot["workspace_id"],
        "at": stamp(moment),
        "from_agent": from_agent.strip(),
        "to_agent": to_agent.strip(),
        "reason": reason,
        "next_action": next_action.strip(),
        "snapshot_ref": snapshot_ref(snapshot),
        "snapshot_digest": snapshot["digest"],
        "snapshot_version": snapshot["version"],
        "anchor_sha256": anchor_sha,
        "evidence": [str(item) for item in evidence] or [
            f"snapshot:{snapshot['digest']}", f"anchor:{anchor_sha}"],
        "reply_due_at": reply_due_at,
    }
    body["digest"] = digest({key: body[key] for key in body if key != "digest"})
    return body


def verify(packet, *, workspace_id=None):
    """Reject a packet that is malformed, re-pointed or from another workspace."""
    if not isinstance(packet, dict) or packet.get("schema") != HANDOFF_SCHEMA:
        raise ValueError("unknown handoff schema")
    validate_workspace_id(packet.get("workspace_id"))
    if workspace_id and packet["workspace_id"] != workspace_id:
        raise ValueError("WORKSPACE_MISMATCH")
    if packet.get("reason") not in REASONS:
        raise ValueError("unknown handoff reason")
    for field in ("snapshot_digest", "anchor_sha256"):
        if not isinstance(packet.get(field), str) or not _HEX.fullmatch(packet[field]):
            raise ValueError(f"{field} must be a sha256 digest")
    if not isinstance(packet.get("next_action"), str) or not packet["next_action"].strip():
        raise ValueError("next_action is required")
    if not isinstance(packet.get("evidence"), list) or not packet["evidence"]:
        raise ValueError("evidence must be a non-empty list")
    if not isinstance(packet.get("snapshot_ref"), str) or \
            not packet["snapshot_ref"].endswith(packet["snapshot_digest"]):
        raise ValueError("snapshot_ref does not pin the declared digest")
    if packet.get("from_agent") == packet.get("to_agent"):
        raise ValueError("handoff target must differ from the current agent")
    actual = digest({key: packet[key] for key in packet if key != "digest"})
    if packet.get("digest") != actual:
        raise ValueError("HANDOFF_DIGEST_MISMATCH")
    return {"valid": True, "workspace_id": packet["workspace_id"],
            "handoff_id": packet["handoff_id"], "reason": packet["reason"],
            "to_agent": packet["to_agent"], "snapshot_digest": packet["snapshot_digest"]}


def accept(packet, *, agent, capabilities, snapshot=None):
    """Accept only for the named agent, with a matching capability and snapshot."""
    report = verify(packet)
    if not isinstance(agent, str) or agent.strip() != packet["to_agent"]:
        raise ValueError("HANDOFF_TARGET_MISMATCH")
    if not isinstance(capabilities, (list, tuple, set)):
        raise ValueError("capabilities must be a collection")
    needed = packet.get("required_capability")
    if needed and needed not in set(capabilities):
        raise ValueError(f"MISSING_CAPABILITY:{needed}")
    if snapshot is not None:
        verify_snapshot(snapshot, expected_anchor_sha256=packet["anchor_sha256"])
        if snapshot["digest"] != packet["snapshot_digest"]:
            raise ValueError("HANDOFF_SNAPSHOT_MISMATCH")
        if snapshot["workspace_id"] != packet["workspace_id"]:
            raise ValueError("WORKSPACE_MISMATCH")
    return {"accepted": True, "handoff_id": report["handoff_id"],
            "workspace_id": report["workspace_id"], "agent": agent.strip(),
            "snapshot_digest": report["snapshot_digest"]}
