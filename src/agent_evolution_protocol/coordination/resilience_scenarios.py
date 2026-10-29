"""Deterministic, side-effect-free chaos scenarios for acceptance tests."""
from __future__ import annotations

from pathlib import Path
from .mission_intake import create
from .mission_supervisor import inspect
from .ack_tracker import open_request, wait_for_ack
from .recovery_policy import decide


def run(root: str | Path, scenario: str) -> dict:
    root = Path(root)
    if scenario == "all_agents_offline":
        mission = create(root, goal="chaos test")
        return {"scenario": scenario, "mission_id": mission["mission_id"],
                "supervisor": inspect(root), "expected": "assign_or_recovery_review"}
    if scenario == "ack_timeout":
        event = "chaos-ack-timeout"
        open_request(root, event_id=event, agent="offline", lease_seconds=1)
        ack = wait_for_ack(root, event_id=event, timeout_seconds=0)
        return {"scenario": scenario, "ack": ack, "recovery": decide(status="ACK_TIMEOUT", attempts=1)}
    if scenario == "restart_recovery":
        mission = create(root, goal="restart test")
        # Re-read from disk, representing a fresh supervisor process.
        return {"scenario": scenario, "mission_id": mission["mission_id"], "recovered": bool(inspect(root)["missions"])}
    raise ValueError(f"unknown scenario: {scenario}")
