"""Event-driven recovery kernel for the local multi-agent collaboration hub.

The kernel is deliberately model-free: it records events, leases, side effects,
checkpoints, and conversation affinity.  It never polls an agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from uuid import uuid4


INTENT_STATES = {"ACTIVE", "PAUSE_REQUESTED", "PAUSED", "CANCEL_REQUESTED", "CANCELLED", "COMPLETED"}
TASK_STATES = {
    "QUEUED", "CLAIMED", "STARTING", "RUNNING", "WAITING_EXTERNAL", "WAITING_USER",
    "CHECKPOINTING", "HANDOFF_REQUESTED", "HANDOFF_READY", "TAKEN_OVER", "VERIFYING",
    "SUCCEEDED", "FAILED", "ABANDONED", "SUSPECTED_STALL", "CONFIRMED_STALL",
}
EXECUTOR_STATES = {"OFFLINE", "AVAILABLE", "RESERVED", "BUSY", "QUIESCING", "UNRESPONSIVE", "RECOVERING", "DRAINED"}
CHANNEL_STATES = {"UNKNOWN", "HEALTHY", "DEGRADED", "BROKEN", "RECOVERING"}
LEASE_TYPES = {"TASK_WRITE_LEASE", "UPLOAD_INTENT_LEASE", "BROWSER_SESSION_LEASE"}
OP_STATES = {"RESERVED", "STARTED", "COMMITTED", "UNKNOWN", "ROLLED_BACK"}
OP_TRANSITIONS = {
    "RESERVED": {"STARTED", "ROLLED_BACK"},
    "STARTED": {"COMMITTED", "UNKNOWN", "ROLLED_BACK"},
    "UNKNOWN": {"COMMITTED", "ROLLED_BACK"},
    "COMMITTED": set(),
    "ROLLED_BACK": set(),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="milliseconds")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _request_hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ContinuationPlan:
    action: str
    provider: str
    session_id: str | None
    reason: str


class ClusterKernel:
    """Small append-only state kernel with monotonic lease fencing."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.events_path = self.root / "events.jsonl"
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    def events(self) -> list[dict]:
        if not self.events_path.exists():
            return []
        result: list[dict] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                result.append(item)
        return result

    def append(self, kind: str, **payload: object) -> dict:
        with self._lock:
            prior = self.events()
            previous_hash = prior[-1]["hash"] if prior else "GENESIS"
            body = {"id": uuid4().hex, "at": iso_now(), "kind": kind, **payload, "previous_hash": previous_hash}
            body["hash"] = _request_hash(body)
            with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(_canonical(body) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return body

    def verify_chain(self) -> bool:
        previous = "GENESIS"
        for event in self.events():
            claimed = event.get("hash")
            body = {key: value for key, value in event.items() if key != "hash"}
            if event.get("previous_hash") != previous or claimed != _request_hash(body):
                return False
            previous = str(claimed)
        return True

    def record_state(self, axis: str, state: str, *, task_id: str = "", actor: str = "system", command_id: str = "", **details: object) -> dict:
        allowed = {"intent": INTENT_STATES, "task": TASK_STATES, "executor": EXECUTOR_STATES, "channel": CHANNEL_STATES}
        if axis not in allowed or state not in allowed[axis]:
            raise ValueError(f"invalid {axis} state: {state}")
        if axis == "intent" and state in {"PAUSE_REQUESTED", "CANCEL_REQUESTED", "CANCELLED"}:
            if actor != "user" or not command_id:
                raise PermissionError("pause/cancel requires an explicit user command_id")
        return self.append("STATE", axis=axis, state=state, task_id=task_id, actor=actor, command_id=command_id, details=details)

    def _latest(self, kind: str, predicate) -> dict | None:
        for event in reversed(self.events()):
            if event.get("kind") == kind and predicate(event):
                return event
        return None

    def acquire_lease(self, lease_type: str, resource: str, executor_id: str, *, task_id: str = "", artifact_sha: str = "", ttl_seconds: int = 300) -> dict:
        if lease_type not in LEASE_TYPES:
            raise ValueError("invalid lease type")
        if lease_type == "UPLOAD_INTENT_LEASE" and (not task_id or not artifact_sha):
            raise ValueError("upload lease requires task_id and artifact_sha")
        now = utc_now()
        history = [event for event in self.events() if event.get("kind", "").startswith("LEASE_") and event.get("lease_type") == lease_type and event.get("resource") == resource]
        epoch = max((int(event.get("lease_epoch", 0)) for event in history), default=0) + 1
        current = next((event for event in reversed(history) if event.get("kind") in {"LEASE_ACQUIRED", "LEASE_RENEWED", "LEASE_RELEASED", "LEASE_REVOKED"}), None)
        if current and current["kind"] in {"LEASE_ACQUIRED", "LEASE_RENEWED"}:
            expires = datetime.fromisoformat(str(current["expires_at"]))
            if expires > now:
                raise RuntimeError(f"LEASE_CONFLICT:{current['executor_id']}:{current['lease_epoch']}")
        return self.append(
            "LEASE_ACQUIRED", lease_type=lease_type, resource=resource, executor_id=executor_id,
            task_id=task_id, artifact_sha=artifact_sha, lease_epoch=epoch,
            expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(timespec="milliseconds"),
        )

    def release_lease(self, lease_type: str, resource: str, executor_id: str, lease_epoch: int) -> dict:
        active = next((
            event for event in reversed(self.events())
            if event.get("kind", "").startswith("LEASE_")
            and event.get("lease_type") == lease_type
            and event.get("resource") == resource
        ), None)
        if (
            not active or active.get("kind") not in {"LEASE_ACQUIRED", "LEASE_RENEWED"}
            or active.get("executor_id") != executor_id
            or int(active.get("lease_epoch", 0)) != lease_epoch
        ):
            raise RuntimeError("FENCED_LEASE")
        return self.append("LEASE_RELEASED", lease_type=lease_type, resource=resource, executor_id=executor_id, lease_epoch=lease_epoch)

    def reserve_operation(self, operation_key: str, request: dict) -> dict:
        existing = self._latest("OPERATION", lambda event: event.get("operation_key") == operation_key)
        digest = _request_hash(request)
        if existing:
            if existing.get("request_hash") != digest:
                raise RuntimeError("IDEMPOTENCY_CONFLICT")
            return existing
        return self.append("OPERATION", operation_key=operation_key, state="RESERVED", request=request, request_hash=digest)

    def transition_operation(self, operation_key: str, state: str, **evidence: object) -> dict:
        if state not in OP_STATES:
            raise ValueError("invalid operation state")
        current = self._latest("OPERATION", lambda event: event.get("operation_key") == operation_key)
        if not current:
            raise KeyError(operation_key)
        if state not in OP_TRANSITIONS[str(current["state"])]:
            raise RuntimeError(f"INVALID_OPERATION_TRANSITION:{current['state']}->{state}")
        return self.append("OPERATION", operation_key=operation_key, state=state, request=current["request"], request_hash=current["request_hash"], evidence=evidence)

    def bind_session(self, agent: str, provider: str, workspace: str, session_id: str, *, resume_supported: bool = True) -> dict:
        if provider not in {"codex", "hermes", "qoder", "codebuddy", "github_copilot"}:
            raise ValueError("invalid provider")
        if not session_id.strip():
            raise ValueError("session_id required")
        return self.append("SESSION_BOUND", agent=agent.lower(), provider=provider, workspace=str(Path(workspace).resolve()), session_id=session_id, resume_supported=resume_supported)

    def continuation_plan(self, agent: str, workspace: str, *, requested_session_id: str = "", allow_new_session: bool = False) -> ContinuationPlan:
        resolved = str(Path(workspace).resolve())
        binding = self._latest("SESSION_BOUND", lambda event: event.get("agent") == agent.lower() and event.get("workspace") == resolved)
        session_id = requested_session_id or (str(binding["session_id"]) if binding else "")
        provider = str(binding.get("provider") if binding else ("hermes" if agent.lower() == "workbuddy" else agent.lower()))
        supported = bool(binding.get("resume_supported", True)) if binding else provider in {"codex", "hermes"}
        if session_id and supported:
            return ContinuationPlan("RESUME_EXISTING", provider, session_id, "trusted session affinity found")
        if allow_new_session:
            return ContinuationPlan("NEW_SESSION_WITH_HANDOFF", provider, None, "explicit new-session override")
        return ContinuationPlan("BLOCKED_HANDOFF_REQUIRED", provider, None, "no resumable target session; automatic new session is forbidden")

    def checkpoint(self, task_id: str, executor_id: str, payload: dict) -> dict:
        required = {"completed", "next_step", "artifacts", "pending_operations", "unknowns"}
        missing = sorted(required - payload.keys())
        if missing:
            raise ValueError("checkpoint missing: " + ", ".join(missing))
        return self.append("CHECKPOINT_WRITTEN", task_id=task_id, executor_id=executor_id, checkpoint=payload, checkpoint_hash=_request_hash(payload))
