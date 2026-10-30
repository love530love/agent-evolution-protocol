"""Evidence-driven execution-plane routing.

The coordination kernel can schedule work, but it must not mistake a discovered
tool, an open window, or text in a composer for a completed delivery.  This
module is the small, provider-agnostic contract between the scheduler and an
execution adapter.  It is intentionally side-effect free: adapters use it to
plan and validate, while the adapter remains responsible for the actual
browser/desktop/terminal action.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


STAGES = ("candidate", "probed", "session-accepted", "task-tested", "retired")
OBSERVATION_GAPS = {
    "no_session": "SESSION_BINDING_REQUIRED",
    "no_input": "INPUT_CHANNEL_UNAVAILABLE",
    "input_only": "MESSAGE_SEND_UNCONFIRMED",
    "visible_no_ack": "AGENT_ACK_MISSING",
    "ack": "DELIVERY_ACCEPTED",
}


@dataclass(frozen=True)
class DeliveryEvidence:
    """Minimal evidence collected by an execution adapter."""

    provider: str
    workspace_id: str
    session_id: str
    event_id: str
    operation: str
    input_submitted: bool = False
    message_visible: bool = False
    agent_ack: bool = False
    error: str = ""

    def validate(self) -> None:
        for name in ("provider", "workspace_id", "session_id", "event_id", "operation"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        for name in ("input_submitted", "message_visible", "agent_ack"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a bool")
        if self.agent_ack and not (self.input_submitted and self.message_visible):
            raise ValueError("agent_ack requires the preceding evidence chain")

    @property
    def state(self) -> str:
        self.validate()
        if self.agent_ack:
            return "AGENT_ACK"
        if self.message_visible:
            return "MESSAGE_VISIBLE"
        if self.input_submitted:
            return "INPUT_SUBMITTED"
        return "UNKNOWN"

    @property
    def accepted(self) -> bool:
        return self.state == "AGENT_ACK"


def classify_observation(*, session_bound: bool, input_submitted: bool,
                         message_visible: bool, agent_ack: bool) -> str:
    """Turn adapter observations into a stable recovery reason.

    This is deliberately stricter than a UI screenshot.  A provider that cannot
    prove the original session remains a planner candidate, never a delivery
    candidate.
    """
    flags = (session_bound, input_submitted, message_visible, agent_ack)
    if not all(isinstance(flag, bool) for flag in flags):
        raise ValueError("observation flags must be bools")
    if agent_ack and not (session_bound and input_submitted and message_visible):
        raise ValueError("ACK cannot be accepted without the preceding evidence")
    if not session_bound:
        return OBSERVATION_GAPS["no_session"]
    if not input_submitted:
        return OBSERVATION_GAPS["no_input"]
    if not message_visible:
        return OBSERVATION_GAPS["input_only"]
    if not agent_ack:
        return OBSERVATION_GAPS["visible_no_ack"]
    return OBSERVATION_GAPS["ack"]


def _minimum_stage(operation: str) -> str:
    if not isinstance(operation, str) or not operation.strip():
        raise ValueError("operation is required")
    # Read-only inspection can use a session-accepted adapter.  Any operation
    # that can change state requires a task-tested provider.
    return "session-accepted" if operation in {"observe", "read", "locate", "screenshot"} else "task-tested"


def route_candidates(catalog: dict[str, Any], *, layer: str, operation: str,
                     evidence: Iterable[DeliveryEvidence] = ()) -> dict[str, Any]:
    """Return only providers that are eligible for the requested operation.

    Candidate/probed rows are intentionally excluded from production dispatch.
    The returned object is a plan; it never starts a process or sends input.
    """
    if not isinstance(catalog, dict) or not isinstance(catalog.get("providers"), list):
        raise ValueError("catalog.providers must be a list")
    minimum = _minimum_stage(operation)
    rank = {stage: index for index, stage in enumerate(STAGES)}
    evidence_by_provider = {item.provider: item for item in evidence}
    rows: list[dict[str, Any]] = []
    for row in catalog["providers"]:
        if not isinstance(row, dict) or row.get("layer") != layer:
            continue
        if operation not in row.get("capabilities", []):
            continue
        status = row.get("status")
        if status not in STAGES or status == "retired" or rank[status] < rank[minimum]:
            continue
        proof = evidence_by_provider.get(row.get("id"))
        if proof is not None and proof.accepted is False and operation not in {"observe", "read", "locate", "screenshot"}:
            continue
        rows.append({"provider": row["id"], "status": status,
                     "minimum_stage": minimum,
                     "evidence_state": proof.state if proof is not None else "UNVERIFIED"})
    rows.sort(key=lambda item: (0 if item["status"] == "task-tested" else 1, item["provider"]))
    return {"status": "READY_TO_PLAN" if rows else "NO_PRODUCTION_PROVIDER",
            "layer": layer, "operation": operation,
            "minimum_stage": minimum, "candidates": rows,
            "execution_started": False}
