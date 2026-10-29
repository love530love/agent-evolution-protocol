"""UIA wake adapter with strict discovery gates."""
from __future__ import annotations

from .desktop_discovery import visible_hosts


def prepare(agent: str) -> dict:
    candidates = [r for r in visible_hosts() if r["agent"] == agent]
    if not candidates:
        return {"status": "NO_VISIBLE_WINDOW", "agent": agent, "candidates": []}
    if len(candidates) != 1:
        return {"status": "AMBIGUOUS_WINDOW", "agent": agent, "candidates": candidates}
    row = candidates[0]
    return {"status": "UIA_READY", "agent": agent, "target": row,
            "next": "capture_accessibility_tree_then_relocate_session"}


def deliver(agent: str, *, event_id: str, text: str, execute: bool = False) -> dict:
    target = prepare(agent)
    if target["status"] != "UIA_READY": return {**target, "event_id": event_id}
    if not execute: return {"status": "PREVIEW", "event_id": event_id, "target": target["target"]}
    return {"status": "UIA_EXECUTOR_REQUIRED", "event_id": event_id, "target": target["target"],
            "reason": "fresh accessibility tree required; no coordinate fallback"}
