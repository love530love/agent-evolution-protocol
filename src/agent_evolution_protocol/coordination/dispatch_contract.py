"""Strict, reusable dispatch envelope for heterogeneous Agent handoffs.

The envelope is intentionally independent from any provider UI.  A desktop,
MCP, CDP or A2A adapter may carry it, but every adapter must preserve the same
event id, session policy, preconditions, evidence requirements and retry fence.
"""
from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any


SCHEMA = "aep-dispatch-envelope-v1"
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
PRIORITIES = {"urgent", "normal", "low"}
SESSION_MODES = {"sticky", "anchor-required", "new-session"}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{field} must be a non-empty list of strings")
    return [item.strip() for item in value]


def validate(envelope: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise ValueError("dispatch envelope must be an object")
    if envelope.get("schema") != SCHEMA:
        raise ValueError("invalid dispatch envelope schema")
    for field in ("order_id", "event_id", "workspace_id"):
        value = _text(envelope.get(field), field)
        if not IDENTIFIER.fullmatch(value):
            raise ValueError(f"invalid {field}")
    for field in ("sender", "target", "task", "instruction"):
        _text(envelope.get(field), field)
    if envelope.get("priority") not in PRIORITIES:
        raise ValueError("invalid priority")
    for field in ("created_at", "expires_at"):
        value = _text(envelope.get(field), field)
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid {field}") from exc
    if envelope["event_id"] not in envelope["instruction"]:
        raise ValueError("instruction must carry event_id")

    policy = envelope.get("session_policy")
    if not isinstance(policy, dict):
        raise ValueError("session_policy must be an object")
    mode = policy.get("mode")
    if mode not in SESSION_MODES:
        raise ValueError("invalid session_policy.mode")
    if not isinstance(policy.get("allow_new_session"), bool):
        raise ValueError("session_policy.allow_new_session must be bool")
    if not isinstance(policy.get("same_workspace"), bool):
        raise ValueError("session_policy.same_workspace must be bool")
    if mode in {"sticky", "anchor-required"} and policy["allow_new_session"]:
        raise ValueError("sticky/anchor-required dispatch cannot allow a new session")
    if mode == "new-session" and not policy["allow_new_session"]:
        raise ValueError("new-session dispatch must explicitly allow a new session")
    if policy["allow_new_session"] and not policy.get("handoff_packet_ref"):
        raise ValueError("new-session dispatch requires handoff_packet_ref")

    for field in ("preconditions", "acceptance", "evidence_required", "stop_conditions"):
        _list(envelope.get(field), field)
    retry = envelope.get("retry_policy")
    if not isinstance(retry, dict):
        raise ValueError("retry_policy must be an object")
    attempts = retry.get("max_attempts")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ValueError("retry_policy.max_attempts must be a positive integer")
    if not isinstance(retry.get("retry_only_preinput"), bool):
        raise ValueError("retry_policy.retry_only_preinput must be bool")
    if not isinstance(retry.get("same_event_id"), bool):
        raise ValueError("retry_policy.same_event_id must be bool")
    if not retry["retry_only_preinput"] or not retry["same_event_id"]:
        raise ValueError("dispatch retries must be pre-input-only and event-id stable")

    legacy = bool(envelope.get("legacy_compat", False))
    if strict and legacy:
        raise ValueError("strict dispatch rejects legacy_compat envelope")
    if strict and envelope["workspace_id"] == "legacy-default":
        raise ValueError("strict dispatch requires an explicit workspace_id")
    return json.loads(json.dumps(envelope, ensure_ascii=False, allow_nan=False))


def build(*, order_id: str, event_id: str, workspace_id: str, sender: str,
          target: str, task: str, instruction: str, priority: str,
          created_at: str, expires_at: str, session_id: str = "",
          conversation_id: str = "", model_marker: str = "",
          session_mode: str = "sticky", allow_new_session: bool = False,
          handoff_packet_ref: str = "", objective: str = "",
          preconditions: list[str] | None = None,
          acceptance: list[str] | None = None,
          evidence_required: list[str] | None = None,
          stop_conditions: list[str] | None = None,
          max_attempts: int = 1, legacy_compat: bool = False) -> dict[str, Any]:
    payload = {
        "schema": SCHEMA,
        "order_id": order_id,
        "event_id": event_id,
        "workspace_id": workspace_id or "legacy-default",
        "sender": sender,
        "target": target,
        "task": task,
        "instruction": instruction,
        "objective": objective or task,
        "priority": priority,
        "created_at": created_at,
        "expires_at": expires_at,
        "session_policy": {
            "mode": session_mode,
            "allow_new_session": bool(allow_new_session),
            "same_workspace": True,
            "session_id": session_id,
            "conversation_id": conversation_id,
            "model_marker": model_marker,
            **({"handoff_packet_ref": handoff_packet_ref} if handoff_packet_ref else {}),
        },
        "preconditions": preconditions or ["workspace_bound", "session_anchor_resolved", "target_ready"],
        "acceptance": acceptance or ["message_visible", "target_agent_ack"],
        "evidence_required": evidence_required or ["event_id_in_same_session", "composer_cleared", "agent_ack"],
        "retry_policy": {
            "max_attempts": max_attempts,
            "retry_only_preinput": True,
            "same_event_id": True,
        },
        "stop_conditions": stop_conditions or ["agent_busy", "session_not_verified", "send_uncertain"],
        "side_effects": ["desktop_input", "agent_execution"],
        "legacy_compat": bool(legacy_compat or not workspace_id),
    }
    return validate(payload)


def from_order(order: dict[str, Any]) -> dict[str, Any]:
    dispatch = order.get("dispatch")
    if isinstance(dispatch, dict):
        return validate(dispatch)
    raise ValueError("order has no dispatch envelope")
