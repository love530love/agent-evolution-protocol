"""Universal task envelope shared by heterogeneous Agents."""
from __future__ import annotations
from typing import Any

REQUIRED = {"task_id", "goal", "requester", "phase", "next_action"}
PHASES = {"discover", "debate", "execute", "verify", "handoff", "done", "blocked"}

def validate(envelope: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(envelope, dict) or not REQUIRED.issubset(envelope): raise ValueError("task envelope missing required fields")
    if not isinstance(envelope["task_id"], str) or not envelope["task_id"]: raise ValueError("invalid task_id")
    if envelope["phase"] not in PHASES: raise ValueError("invalid phase")
    for key in ("goal", "requester", "next_action"):
        if not isinstance(envelope[key], str) or not envelope[key].strip(): raise ValueError(f"invalid {key}")
    for key, default in (("context", {}), ("evidence", []), ("dissent", []), ("constraints", []), ("assignees", [])):
        if key in envelope and not isinstance(envelope[key], type(default)): raise ValueError(f"invalid {key}")
    result = dict(envelope)
    result.setdefault("context", {}); result.setdefault("evidence", []); result.setdefault("dissent", []); result.setdefault("constraints", []); result.setdefault("assignees", [])
    return result

def handoff(envelope: dict[str, Any], *, assignee: str, next_action: str, evidence: list[str] | None = None) -> dict[str, Any]:
    result = validate(envelope)
    result["phase"] = "handoff"; result["assignees"] = list(dict.fromkeys([*result["assignees"], assignee])); result["next_action"] = next_action
    if evidence: result["evidence"] = [*result["evidence"], *evidence]
    return result
