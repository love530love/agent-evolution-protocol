"""Common provider readback/ACK receipt contract."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

STATES = {"INPUT_SUBMITTED", "MESSAGE_VISIBLE", "AGENT_ACK", "FAILED", "UNKNOWN"}

def receipt(*, provider: str, event_id: str, state: str, observed: bool,
            ack: bool = False, evidence: list[str] | None = None, error: str = "") -> dict[str, Any]:
    if not provider or not event_id: raise ValueError("provider and event_id are required")
    if state not in STATES: raise ValueError("invalid readback state")
    if not isinstance(observed, bool) or not isinstance(ack, bool): raise ValueError("observed and ack must be bool")
    paths = evidence or []
    if not isinstance(paths, list) or any(not isinstance(p, str) or not p for p in paths): raise ValueError("evidence must be a string list")
    if state == "AGENT_ACK" and not ack: raise ValueError("AGENT_ACK requires ack=true")
    if ack and state != "AGENT_ACK": raise ValueError("ack requires AGENT_ACK")
    return {"schema": "aep-readback-v1", "provider": provider, "event_id": event_id,
            "state": state, "observed": observed, "ack": ack, "evidence": paths,
            "error": error, "at": datetime.now(timezone.utc).isoformat()}

def accepted(value: dict[str, Any]) -> bool:
    return isinstance(value, dict) and value.get("schema") == "aep-readback-v1" and value.get("state") == "AGENT_ACK" and value.get("ack") is True and bool(value.get("event_id"))
