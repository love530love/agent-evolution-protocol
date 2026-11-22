"""Command-handoff state machine for quota / usage / time constrained fleets.

When any vendor agent hits a limit, any other agent (including a weak one) must
be able to take over command, split work and deliver -- the project must not
stall because of a single provider's ceiling.

This module is deliberately self-contained. It depends only on the standard
library plus ``diagnosis``'s signal vocabulary, and persists as a single JSON
file written atomically (``tempfile`` + ``os.replace``). It does not import
``foundation`` (which may be edited concurrently), does no network, no
subprocess, no model call and touches no desktop resource.

Contract properties
-------------------
* **fail-closed** -- missing evidence, an illegal signal, an unknown role or an
  unauthorised takeover all raise ``ValueError``; nothing is guessed.
* **single writer** -- a role has at most one ``ACTIVE`` holder. Takeover is
  only legal from ``YIELDED``/``LIMITED`` so two agents can never be ACTIVE on
  the same role at once.
* **recomputable** -- every id and sequence derives from canonical content
  (no wall clock, no random). The same inputs always yield the same output.
* **auditable** -- each mutation appends a hash-linked event; ``status`` can
  verify the whole chain.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .diagnosis import CAPACITY_SIGNALS, QUOTA_SIGNALS

SCHEMA = "aep-command-handoff-v1"
STATE_FILE = "command_handoff/state.json"

# Roles are functional, never vendor names, and extensible via register_role().
DEFAULT_ROLES = {
    "commander": "command",
    "scout": "recon",
    "github": "repo_intel",
    "builder": "build",
}

ACTIVE = "ACTIVE"
LIMITED = "LIMITED"
YIELDED = "YIELDED"
TAKING_OVER = "TAKING_OVER"
STANDBY = "STANDBY"
STATES = (ACTIVE, LIMITED, YIELDED, TAKING_OVER, STANDBY)

#: A role in one of these states may be taken over.
TAKEOVER_STATES = frozenset({YIELDED, LIMITED})
#: Warning-grade signals degrade a holder to LIMITED rather than YIELDED.
WARN_SIGNALS = frozenset(set(CAPACITY_SIGNALS) - set(QUOTA_SIGNALS))

ROLE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
CAPABILITY_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_EVENT_ID_FIELDS = ("seq", "schema", "kind", "actor", "role", "payload", "prev_sha256")


# --------------------------------------------------------------------------- #
# Persistence helpers (self-contained: json + os.replace, no foundation)
# --------------------------------------------------------------------------- #
def _canonical_bytes(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _digest(payload):
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def _state_path(coord_root):
    return Path(coord_root) / STATE_FILE


def _fresh_state():
    roles = {}
    for name, capability in DEFAULT_ROLES.items():
        roles[name] = {
            "state": STANDBY,
            "holder": None,
            "required_capability": capability,
            "candidates": {},
            "handoffs": [],
            "updated_seq": 0,
        }
    return {"schema": SCHEMA, "seq": 0, "roles": roles, "events": []}


def _load(coord_root):
    path = _state_path(coord_root)
    if not path.is_file():
        return _fresh_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError("CORRUPT_STATE") from exc
    if not isinstance(state, dict) or state.get("schema") != SCHEMA:
        raise ValueError("UNKNOWN_STATE_SCHEMA")
    if not isinstance(state.get("roles"), dict) or not isinstance(state.get("events"), list):
        raise ValueError("MALFORMED_STATE")
    return state


def _save(coord_root, state):
    _atomic_write(_state_path(coord_root), state)


# --------------------------------------------------------------------------- #
# Validation helpers -- every one fails closed
# --------------------------------------------------------------------------- #
def _require_agent(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _require_evidence(evidence):
    if isinstance(evidence, str):
        items = [evidence]
    elif isinstance(evidence, (list, tuple)):
        items = list(evidence)
    else:
        raise ValueError("EVIDENCE_REQUIRED")
    cleaned = [item.strip() for item in items
               if isinstance(item, str) and item.strip()]
    if not cleaned:
        raise ValueError("EVIDENCE_REQUIRED")
    return cleaned


def _require_role(state, role):
    if not isinstance(role, str) or not ROLE_NAME.fullmatch(role):
        raise ValueError("INVALID_ROLE")
    if role not in state["roles"]:
        raise ValueError(f"UNKNOWN_ROLE:{role}")
    return role


def _covers(capability, required):
    """Does a declared capability string cover a role's required token?"""
    if not isinstance(capability, str) or not capability.strip():
        return False
    tokens = {token for token in re.split(r"[,\s]+", capability.strip()) if token}
    return required in tokens


def _append_event(state, kind, actor, role, payload):
    seq = state["seq"] + 1
    previous = state["events"][-1]["sha256"] if state["events"] else ""
    event = {"seq": seq, "schema": SCHEMA, "kind": kind, "actor": actor,
             "role": role, "payload": payload, "prev_sha256": previous}
    event["event_id"] = "E" + _digest({key: event[key] for key in _EVENT_ID_FIELDS})[:15]
    event["sha256"] = _digest({key: event[key] for key in event if key != "sha256"})
    state["events"].append(event)
    state["seq"] = seq
    return event


def _verify_chain(state):
    previous = ""
    for index, event in enumerate(state["events"], start=1):
        if event.get("seq") != index or event.get("prev_sha256") != previous:
            return False
        if event.get("event_id") != "E" + _digest(
                {key: event.get(key) for key in _EVENT_ID_FIELDS})[:15]:
            return False
        if event.get("sha256") != _digest(
                {key: event[key] for key in event if key != "sha256"}):
            return False
        previous = event["sha256"]
    return True


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def register_role(coord_root, *, role, capability):
    """Extend the role registry. Re-registration with the same token is idempotent."""
    state = _load(coord_root)
    if not isinstance(role, str) or not ROLE_NAME.fullmatch(role):
        raise ValueError("INVALID_ROLE")
    if not isinstance(capability, str) or not CAPABILITY_TOKEN.fullmatch(capability.strip()):
        raise ValueError("INVALID_CAPABILITY")
    capability = capability.strip()
    if role in state["roles"]:
        if state["roles"][role]["required_capability"] == capability:
            return {"ok": True, "action": "register_role", "role": role,
                    "capability": capability, "idempotent": True, "seq": state["seq"]}
        raise ValueError("ROLE_CAPABILITY_CONFLICT")
    state["roles"][role] = {
        "state": STANDBY, "holder": None, "required_capability": capability,
        "candidates": {}, "handoffs": [], "updated_seq": state["seq"] + 1,
    }
    event = _append_event(state, "register_role", "system", role,
                          {"capability": capability})
    _save(coord_root, state)
    return {"ok": True, "action": "register_role", "role": role,
            "capability": capability, "idempotent": False,
            "seq": event["seq"], "event_id": event["event_id"]}


def claim(coord_root, *, agent, role, capability, evidence):
    """Establish the initial single ACTIVE holder of a STANDBY role."""
    state = _load(coord_root)
    role = _require_role(state, role)
    agent = _require_agent(agent, "agent")
    evidence = _require_evidence(evidence)
    record = state["roles"][role]
    if record["holder"] is not None:
        raise ValueError("ROLE_ALREADY_HELD")
    required = record["required_capability"]
    if not _covers(capability, required):
        raise ValueError(f"CAPABILITY_MISMATCH:{required}")
    record["state"] = ACTIVE
    record["holder"] = agent
    record["updated_seq"] = state["seq"] + 1
    event = _append_event(state, "claim", agent, role,
                          {"capability": capability, "evidence": evidence, "state": ACTIVE})
    _save(coord_root, state)
    return {"ok": True, "action": "claim", "role": role, "agent": agent,
            "state": ACTIVE, "required_capability": required,
            "seq": event["seq"], "event_id": event["event_id"]}


def volunteer(coord_root, *, from_agent, role, capability, reason, evidence):
    """Register an agent (even a weak one) as a candidate for a role.

    Evidence is mandatory; capability may be weak, in which case the candidate
    is recorded as ``eligible=False`` and can never take over.
    """
    state = _load(coord_root)
    role = _require_role(state, role)
    agent = _require_agent(from_agent, "from_agent")
    evidence = _require_evidence(evidence)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("REASON_REQUIRED")
    reason = reason.strip()
    record = state["roles"][role]
    required = record["required_capability"]
    capability_text = capability if isinstance(capability, str) else ""
    eligible = _covers(capability, required)
    candidate = {"agent": agent, "capability": capability_text, "reason": reason,
                 "evidence": evidence, "eligible": eligible, "seq": state["seq"] + 1}
    existing = record["candidates"].get(agent)
    if (existing and existing["evidence"] == evidence
            and existing["capability"] == capability_text
            and existing["reason"] == reason):
        return {"ok": True, "action": "volunteer", "role": role, "agent": agent,
                "eligible": existing["eligible"], "idempotent": True,
                "state": record["state"], "seq": existing["seq"]}
    record["candidates"][agent] = candidate
    record["updated_seq"] = state["seq"] + 1
    event = _append_event(state, "volunteer", agent, role,
                          {"capability": capability_text, "reason": reason,
                           "evidence": evidence, "eligible": eligible})
    _save(coord_root, state)
    return {"ok": True, "action": "volunteer", "role": role, "agent": agent,
            "eligible": eligible, "idempotent": False, "state": record["state"],
            "required_capability": required,
            "seq": event["seq"], "event_id": event["event_id"]}


def yield_role(coord_root, *, from_agent, role, signal, evidence):
    """The ACTIVE holder yields on a limit/capacity signal.

    Hard quota signals produce ``YIELDED``; the warning signal
    (``credit_warning``) produces ``LIMITED``. Both are takeover-able.
    """
    state = _load(coord_root)
    role = _require_role(state, role)
    agent = _require_agent(from_agent, "from_agent")
    evidence = _require_evidence(evidence)
    if signal not in CAPACITY_SIGNALS:
        raise ValueError(f"INVALID_SIGNAL:{signal}")
    record = state["roles"][role]
    if record["holder"] != agent:
        raise ValueError("NOT_HOLDER")
    if record["state"] == TAKING_OVER:
        raise ValueError("ROLE_BUSY")
    new_state = LIMITED if signal in WARN_SIGNALS else YIELDED
    if record["state"] == new_state and record.get("last_signal") == signal:
        return {"ok": True, "action": "yield", "role": role, "from": agent,
                "signal": signal, "state": new_state, "takeover_eligible": True,
                "idempotent": True, "seq": record["updated_seq"]}
    record["state"] = new_state
    record["last_signal"] = signal
    record["updated_seq"] = state["seq"] + 1
    event = _append_event(state, "yield", agent, role,
                          {"signal": signal, "evidence": evidence, "state": new_state})
    _save(coord_root, state)
    return {"ok": True, "action": "yield", "role": role, "from": agent,
            "signal": signal, "state": new_state, "takeover_eligible": True,
            "idempotent": False, "seq": event["seq"], "event_id": event["event_id"]}


def takeover(coord_root, *, to_agent, role, evidence):
    """An eligible candidate takes over a YIELDED/LIMITED role atomically.

    Writes an immutable handoff record (with the ``TAKING_OVER`` transition)
    and installs exactly one new ACTIVE holder. Replaying the identical call
    returns the stored result without mutating state (idempotent).
    """
    state = _load(coord_root)
    role = _require_role(state, role)
    agent = _require_agent(to_agent, "to_agent")
    evidence = _require_evidence(evidence)
    record = state["roles"][role]
    required = record["required_capability"]
    previous_holder = record["holder"]
    handoff_id = "CH-" + _digest(
        {"role": role, "from": previous_holder, "to": agent, "evidence": evidence})[:16]

    # Idempotent replay: a byte-identical takeover that already completed and
    # is still the current state returns its stored result without mutating.
    # The match avoids the volatile previous holder (which changed on takeover).
    if record["holder"] == agent and record["state"] == ACTIVE:
        for handoff in record["handoffs"]:
            if handoff["to"] == agent and handoff["evidence"] == evidence:
                result = dict(handoff["result"])
                result["idempotent"] = True
                return result

    if record["state"] not in TAKEOVER_STATES:
        raise ValueError(f"ROLE_NOT_TAKEOVERABLE:{record['state']}")
    candidate = record["candidates"].get(agent)
    if candidate is None:
        raise ValueError("NOT_A_CANDIDATE")
    if not _covers(candidate.get("capability"), required):
        raise ValueError(f"CAPABILITY_MISMATCH:{required}")

    transition = [record["state"], TAKING_OVER, ACTIVE]
    record["state"] = ACTIVE
    record["holder"] = agent
    record["last_signal"] = None
    record["updated_seq"] = state["seq"] + 1
    event = _append_event(state, "takeover", agent, role,
                          {"from": previous_holder, "handoff_id": handoff_id,
                           "transition": transition, "evidence": evidence})
    result = {"ok": True, "action": "takeover", "role": role, "from": previous_holder,
              "to": agent, "state": ACTIVE, "transition": transition,
              "handoff_id": handoff_id, "seq": event["seq"],
              "event_id": event["event_id"], "idempotent": False}
    record["handoffs"].append({
        "handoff_id": handoff_id, "event_id": event["event_id"], "seq": event["seq"],
        "from": previous_holder, "to": agent, "evidence": evidence, "result": result,
    })
    _save(coord_root, state)
    return result


def status(coord_root):
    """Report every role's holder, candidates, state and complete evidence chain."""
    state = _load(coord_root)
    roles = {}
    for role, record in state["roles"].items():
        roles[role] = {
            "state": record["state"],
            "holder": record.get("holder"),
            "required_capability": record.get("required_capability"),
            "takeover_eligible": record["state"] in TAKEOVER_STATES,
            "candidates": [
                {"agent": name, "capability": candidate.get("capability"),
                 "reason": candidate.get("reason"), "eligible": candidate.get("eligible"),
                 "evidence": list(candidate.get("evidence", [])), "seq": candidate.get("seq")}
                for name, candidate in sorted(record.get("candidates", {}).items())
            ],
            "handoffs": [dict(handoff) for handoff in record.get("handoffs", [])],
            "evidence": [
                {"seq": event["seq"], "kind": event["kind"], "actor": event["actor"],
                 "payload": event["payload"]}
                for event in state["events"] if event["role"] == role
            ],
        }
    return {"schema": SCHEMA, "seq": state["seq"], "roles": roles,
            "events": [dict(event) for event in state["events"]],
            "chain_valid": _verify_chain(state)}
