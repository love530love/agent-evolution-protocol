"""Durable, cross-process fences for writes to an original Agent session.

The project has several ways to reach a desktop Agent (the commander, the
profiled UIA relay, and local CLI adapters).  A process-local mutex or an
order-specific lock cannot protect a conversation when another route is used.
This module gives every compliant route one stable, filesystem-safe lease key:

``workspace identity + provider + target Agent + stable session marker``.

The marker is deliberately *not* a window PID.  PIDs change after a restart;
the verified chat/session marker is the identity that survives it.  A lease is
only a write fence.  It never upgrades process completion to message delivery,
an ACK, or a task result.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time
from typing import Iterable, Iterator
from uuid import uuid4

from agent_evolution_protocol.federation.locking import LockTimeout, workspace_lock


ACTIVE_STATES = frozenset({"STARTING", "RUNNING", "SENDING", "WAITING_REPLY", "UNCERTAIN"})
DEFAULT_LOCK_SECONDS = 5.0
_MAX_SESSION_ID = 2048
LEASE_DIRECTORY = "session_leases"


class SessionLeaseBlocked(RuntimeError):
    """Raised when a durable lease says the original session is unresolved."""

    def __init__(self, previous: dict):
        self.previous = previous
        super().__init__("original session has an active or uncertain lease")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _stable_session_id(value: str) -> str:
    """Normalize presentation whitespace without weakening session identity."""
    if not isinstance(value, str):
        raise ValueError("session_id must be a string")
    normalized = " ".join(value.strip().split())
    if "\0" in normalized or len(normalized) < 4 or len(normalized) > _MAX_SESSION_ID:
        raise ValueError("session_id must be a stable non-empty marker of at least four characters")
    # A native PID is not a session identity: it changes on every restart and
    # would silently defeat a durable original-session fence.  Reject the two
    # forms operators most often paste by accident.  Long provider IDs remain
    # valid, but should be declared with a non-PID prefix in the profile/plan.
    if re.fullmatch(r"(?:pid\s*[:=#-]?\s*)?\d{1,8}", normalized, re.IGNORECASE):
        raise ValueError("session_id must not be a process PID; use a stable chat marker or fingerprint")
    return normalized


def canonical_root(coordination_root: Path | str) -> Path:
    """Return the coordination-wide home for original-session fences.

    Receipts and adapter logs may deliberately live in separate directories.
    The lease cannot: a UIA writer and a CLI writer only mutually exclude each
    other when they use this one canonical root.  The helper is idempotent so
    callers can pass either ``<coordination>`` or ``<coordination>/session_leases``.
    """
    root = Path(coordination_root)
    return root if root.name.casefold() == LEASE_DIRECTORY else root / LEASE_DIRECTORY


def binding(*, workspace_id: str | Path, provider: str, target_agent: str,
            session_id: str) -> dict:
    """Build a canonical session identity suitable for an inter-process lock."""
    fields = {
        "workspace_id": str(workspace_id).strip(),
        "provider": str(provider).strip(),
        "target_agent": str(target_agent).strip(),
        "session_id": _stable_session_id(session_id),
    }
    if any(not value or "\0" in value for name, value in fields.items() if name != "session_id"):
        raise ValueError("workspace_id, provider and target_agent are required")
    return fields


def digest(value: dict) -> str:
    return _digest(value)


def lock_path(root: Path, value: dict) -> Path:
    return Path(root) / "session_locks" / (digest(value) + ".lock")


def state_path(root: Path, value: dict) -> Path:
    return Path(root) / "session_state" / (digest(value) + ".json")


def _legacy_state_path(root: Path, value: dict) -> Path:
    """Locate the v0 external-runner sidecar without trusting it as current.

    Before the shared lease existed, the external runner hashed a ``workspace``
    field rather than ``workspace_id``.  Ignoring an old ``UNCERTAIN`` record
    during an upgrade could cause exactly the duplicate native-chat write this
    module is meant to prevent, so reads retain this one-way compatibility.
    """
    legacy = {
        "workspace": value["workspace_id"],
        "provider": value["provider"],
        "target_agent": value["target_agent"],
        "session_id": value["session_id"],
    }
    return Path(root) / "session_state" / (digest(legacy) + ".json")


def _read_path(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "UNCERTAIN", "error": "session lease state unreadable"}
    if not isinstance(row, dict) or not isinstance(row.get("state"), str):
        return {"state": "UNCERTAIN", "error": "session lease state malformed"}
    return row


def _roots(root: Path, legacy_roots: Iterable[Path | str] = ()) -> list[Path]:
    """Return deterministic, de-duplicated roots without resolving missing paths."""
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in (root, *legacy_roots):
        path = Path(candidate)
        # ``absolute`` works even when a test/portable install has not created
        # the directory yet; ``resolve`` would unnecessarily touch the FS.
        key = os.path.normcase(str(path.absolute()))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _state_candidates(root: Path, value: dict,
                      legacy_roots: Iterable[Path | str] = ()) -> list[tuple[Path, Path, dict, bool]]:
    """Read current and pre-v1 sidecars, including explicitly supplied old roots."""
    rows: list[tuple[Path, Path, dict, bool]] = []
    for candidate_root in _roots(root, legacy_roots):
        for path, legacy_binding in ((state_path(candidate_root, value), False),
                                     (_legacy_state_path(candidate_root, value), True)):
            row = _read_path(path)
            if row is not None:
                rows.append((candidate_root, path, row, legacy_binding))
    return rows


def _annotated(row: dict, *, source_root: Path, source_path: Path,
               legacy_binding: bool) -> dict:
    result = dict(row)
    if legacy_binding:
        result["legacy_binding"] = True
    result["lease_state_root"] = str(source_root)
    result["lease_state_path"] = str(source_path)
    return result


def read_state(root: Path, value: dict, *, legacy_roots: Iterable[Path | str] = ()) -> dict | None:
    """Read the most conservative durable state across canonical/old roots.

    An upgrade must not ignore an unresolved sidecar merely because receipts
    were previously stored under ``external_runs`` rather than the new shared
    ``session_leases`` root.
    """
    candidates = _state_candidates(root, value, legacy_roots)
    for source_root, source_path, row, legacy_binding in candidates:
        if is_active(row):
            return _annotated(row, source_root=source_root, source_path=source_path,
                              legacy_binding=legacy_binding)
    if not candidates:
        return None
    source_root, source_path, row, legacy_binding = candidates[0]
    return _annotated(row, source_root=source_root, source_path=source_path,
                      legacy_binding=legacy_binding)


def is_active(row: dict | None) -> bool:
    return bool(row and row.get("state") in ACTIVE_STATES)


def write_state(root: Path, value: dict, *, event_id: str, state: str,
                request_sha256: str = "", **details: object) -> dict:
    """Persist an auditable session state while the caller owns its lock."""
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id is required")
    if not isinstance(state, str) or not state.strip():
        raise ValueError("state is required")
    row = {
        "schema": "aep-session-lease-v1",
        "binding": value,
        "event_id": event_id,
        "request_sha256": str(request_sha256),
        "state": state,
        "updated_at": time.time(),
        **details,
    }
    _atomic_json(state_path(root, value), row)
    return row


class SessionLease:
    """A held advisory lock plus a durable state writer.

    The lease caller decides which terminal state its adapter actually proved.
    This intentionally keeps the distinction between ``MESSAGE_VISIBLE`` and
    ``AGENT_ACK`` in the execution-plane adapter rather than in the lock.
    """

    def __init__(self, root: Path, value: dict,
                 *, legacy_roots: Iterable[Path | str] = ()):
        self.root = Path(root)
        self.binding = value
        self.legacy_roots = tuple(_roots(self.root, legacy_roots)[1:])
        self.last_state = ""
        self.last_event_id = ""
        self.last_request_sha256 = ""

    @property
    def state_file(self) -> Path:
        return state_path(self.root, self.binding)

    def write(self, *, event_id: str, state: str, request_sha256: str = "",
              **details: object) -> dict:
        row = write_state(self.root, self.binding, event_id=event_id, state=state,
                          request_sha256=request_sha256, **details)
        self.last_state = state
        self.last_event_id = event_id
        self.last_request_sha256 = request_sha256
        return row

    def reconcile(self, *, evidence: str) -> dict:
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("reconciliation requires concrete original-session evidence")
        previous = read_state(self.root, self.binding, legacy_roots=self.legacy_roots)
        if not previous:
            return {"state": "NO_SESSION_STATE", "reconciled": False}
        # Resolve every unresolved sidecar seen through the compatibility
        # lookup.  Writing only the new root would leave an old external-run
        # record active forever; ignoring it would reopen a duplicate-send gap.
        for _source_root, source_path, row, _legacy_binding in _state_candidates(
                self.root, self.binding, self.legacy_roots):
            if not is_active(row):
                continue
            retired = dict(row)
            retired.update(state="RECONCILED", evidence=evidence.strip(),
                           updated_at=time.time())
            _atomic_json(source_path, retired)
        self.write(event_id=str(previous.get("event_id", "reconciled")),
                   request_sha256=str(previous.get("request_sha256", "")),
                   state="RECONCILED", evidence=evidence.strip())
        return {"state": "RECONCILED", "reconciled": True,
                "previous_state": previous.get("state", "")}


@contextmanager
def acquire(root: Path, value: dict, *, timeout: float = DEFAULT_LOCK_SECONDS,
            allow_active: bool = False,
            legacy_roots: Iterable[Path | str] = ()) -> Iterator[SessionLease]:
    """Acquire a binding lock and refuse unresolved prior writes by default."""
    root = Path(root)
    with workspace_lock(lock_path(root, value), timeout=float(timeout)):
        previous = read_state(root, value, legacy_roots=legacy_roots)
        if not allow_active and is_active(previous):
            raise SessionLeaseBlocked(previous)
        yield SessionLease(root, value, legacy_roots=legacy_roots)


def reconcile(root: Path, *, workspace_id: str | Path, provider: str,
              target_agent: str, session_id: str, evidence: str,
              timeout: float = DEFAULT_LOCK_SECONDS,
              legacy_roots: Iterable[Path | str] = ()) -> dict:
    """Record evidence that resolves a prior uncertain session lease.

    It never sends or retries a message.  A later new event may acquire the
    lease only after this evidence is retained.
    """
    value = binding(workspace_id=workspace_id, provider=provider,
                    target_agent=target_agent, session_id=session_id)
    with acquire(root, value, timeout=timeout, allow_active=True,
                 legacy_roots=legacy_roots) as lease:
        return lease.reconcile(evidence=evidence)

