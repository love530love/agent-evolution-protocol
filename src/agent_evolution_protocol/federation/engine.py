"""Federation kernel: one workspace, many agents, resumable across restarts.

The kernel owns the task; agents are interchangeable executors. Every decision
it makes is written to the workspace directory and to the hash-chained event
log, so a new process can rebuild the same picture without reading any chat.

What it does on each :func:`tick`:

1. a paused workspace does nothing;
2. a running task whose agent stopped reporting is diagnosed and handed off;
3. a ready task is offered to the first capable, non-archived adapter;
4. a delivery that fails or exhausts context is classified, retried or handed
   off, and the old agent is archived rather than deleted;
5. foreign messages are quarantined, conflicting conclusions open an
   arbitration task, and everything is recorded.

It never spawns processes and never talks to a vendor API: adapters do that.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..coordination.workspace_identity import new_workspace_id, validate_workspace_id
from . import diagnosis, handoff as handoff_mod, snapshot as snapshot_mod
from .adapter import find, match
from .store import EventLog, atomic_write, read_json, stamp, workspace_dir


STATE_SCHEMA = "aep-federation-state-v1"
TASK_STATES = ("pending", "ready", "running", "done", "blocked", "failed")
TERMINAL = ("done", "failed", "blocked")


def _state_path(root, workspace_id):
    return workspace_dir(root, workspace_id) / "state.json"


def load(root, workspace_id):
    state = read_json(_state_path(root, workspace_id))
    if state is None:
        raise ValueError("workspace state not found")
    if state.get("schema") != STATE_SCHEMA:
        raise ValueError("unknown workspace state schema")
    validate_workspace_id(state.get("workspace_id"))
    if state["workspace_id"] != workspace_id:
        raise ValueError("WORKSPACE_MISMATCH")
    return state


def save(root, state):
    atomic_write(_state_path(root, state["workspace_id"]), state)
    return state


def create(root, *, goal, acceptance, constraints=(), moment=None):
    """Create the workspace, its anchor and its first snapshot."""
    workspace_id = new_workspace_id()
    anchor = snapshot_mod.make_anchor(goal=goal, acceptance=acceptance,
                                      constraints=constraints, moment=moment)
    state = {"schema": STATE_SCHEMA, "workspace_id": workspace_id, "anchor": anchor,
             "created_at": stamp(moment), "paused": False, "tasks": {}, "agents": {},
             "quarantine": [], "handoffs": [], "conflicts": []}
    save(root, state)
    log = EventLog(root, workspace_id)
    log.append("workspace_created", actor="kernel",
               payload={"goal": anchor["goal"], "acceptance": anchor["acceptance"]}, moment=moment)
    checkpoint(root, state, moment=moment, reason="workspace_created")
    return state


def add_task(root, state, *, task_id, requirement, deps=(), moment=None):
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id is required")
    if task_id in state["tasks"]:
        raise ValueError("task already exists")
    if not isinstance(requirement, str) or not requirement.strip():
        raise ValueError("requirement is required")
    for dep in deps:
        if dep not in state["tasks"]:
            raise ValueError(f"unknown dependency {dep}")
    state["tasks"][task_id] = {"id": task_id, "requirement": requirement.strip(),
                               "deps": sorted(set(deps)), "state": "pending",
                               "owner": "", "attempts": 0, "signal": "", "note": ""}
    EventLog(root, state["workspace_id"]).append(
        "task_added", actor="kernel", payload={"task": task_id, "deps": sorted(set(deps))}, moment=moment)
    save(root, state)
    return state["tasks"][task_id]


def ready_tasks(state):
    return sorted(task_id for task_id, task in state["tasks"].items()
                  if task["state"] == "pending"
                  and all(state["tasks"][dep]["state"] == "done" for dep in task["deps"]))


def _blocked_by_failed(state, task_id):
    return [dep for dep in state["tasks"][task_id]["deps"]
            if state["tasks"][dep]["state"] in ("failed", "blocked")]


def _stalled(adapter, now, timeout_seconds):
    beat = adapter.heartbeat(now)
    last = beat.get("last_progress_at")
    if not last:
        return True
    try:
        moment = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (now - moment).total_seconds() > timeout_seconds


def _snapshot(root, state, moment):
    version = snapshot_mod.next_version(root, state["workspace_id"])
    built = snapshot_mod.build(state["workspace_id"], anchor=state["anchor"], dag=state["tasks"],
                               artifacts=state.get("archive", []), moment=moment, version=version)
    snapshot_mod.save(root, built)
    return built


def checkpoint(root, state, *, moment=None, reason="checkpoint"):
    built = _snapshot(root, state, moment)
    EventLog(root, state["workspace_id"]).append(
        "checkpoint", actor="kernel",
        payload={"reason": reason, "version": built["version"], "digest": built["digest"]}, moment=moment)
    return built


def _eligible(state, adapters, requirement, *, exclude=()):
    """Capable agents minus the excluded ones and minus archived agents.

    Archiving happens on every handoff, so without this filter a replaced agent
    would keep receiving new work for the rest of the workspace's life.
    """
    blocked = {item for item in exclude}
    blocked |= {name for name, row in state["agents"].items()
                if row.get("state") == "archived"}
    return match(adapters, requirement, exclude=blocked)


def _handoff(root, state, task, adapters, *, reason, moment, exclude=()):
    """Snapshot, build a packet, and let the first capable agent accept it."""
    built = _snapshot(root, state, moment)
    requirement = task["requirement"]
    candidates = _eligible(state, adapters, requirement,
                           exclude={task["owner"], *exclude} if task["owner"] else exclude)
    if not candidates:
        task["state"] = "blocked"
        task["note"] = "no capable successor agent"
        EventLog(root, state["workspace_id"]).append(
            "handoff_blocked", actor="kernel", payload={"task": task["id"], "reason": reason}, moment=moment)
        return None
    successor = candidates[0]
    current = task["owner"] or "unassigned"
    packet = handoff_mod.packet(built, from_agent=current, to_agent=successor,
                                reason=reason, next_action=requirement, moment=moment)
    target = find(adapters, successor)
    accepted = handoff_mod.accept(packet, agent=successor,
                                 capabilities=target.capabilities(), snapshot=built)
    if task["owner"]:
        state["agents"].setdefault(task["owner"], {})["state"] = "archived"
        state["agents"][task["owner"]]["archived_at"] = stamp(moment)
    task.update({"owner": successor, "state": "running", "attempts": 1, "signal": "", "note": ""})
    state["agents"].setdefault(successor, {})["state"] = "active"
    # The packet itself is the auditable artifact, so it is stored whole rather
    # than reduced to a summary; ``task``/``accepted_by`` are extras the
    # handoff-packet-v1 schema allows.
    state["handoffs"].append({**packet, "task": task["id"],
                              "accepted_by": accepted.get("agent")})
    EventLog(root, state["workspace_id"]).append(
        "handoff_accepted", actor="kernel",
        payload={"task": task["id"], "from": current, "to": successor, "reason": reason,
                 "handoff_id": packet["handoff_id"], "snapshot_digest": built["digest"]}, moment=moment)
    # A takeover is only real once the successor has been offered the task.
    result = target.deliver({"id": task["id"], "requirement": requirement,
                             "workspace_id": state["workspace_id"], "handoff_id": packet["handoff_id"]})
    EventLog(root, state["workspace_id"]).append(
        "handoff_delivery", actor=successor,
        payload={"task": task["id"], "status": result.get("status", ""),
                 "signal": result.get("signal", "")}, moment=moment)
    if result.get("status") != "accepted":
        task["state"] = "blocked"
        task["note"] = f"successor {successor} did not accept: {result.get('status')}"
    return packet


def tick(root, state, adapters, *, now=None, heartbeat_timeout=900, moment=None):
    """Advance the workspace by one bounded step; returns what it did."""
    now = now or datetime.now(timezone.utc)
    moment = moment or now
    actions = []
    if state.get("paused"):
        return {"status": "PAUSED", "actions": []}

    for task_id in sorted(state["tasks"]):
        task = state["tasks"][task_id]
        if task["state"] != "running" or not task["owner"]:
            continue
        adapter = find(adapters, task["owner"])
        if adapter is None or _stalled(adapter, now, heartbeat_timeout):
            decision = diagnosis.classify("no_heartbeat", attempts=task["attempts"])
            packet = _handoff(root, state, task, adapters, reason="stalled", moment=moment)
            actions.append({"task": task_id, "action": decision["action"],
                            "handoff": bool(packet), "reason": decision["reason"]})

    # A pending task whose dependency already failed can never become ready.
    for task_id in sorted(state["tasks"]):
        task = state["tasks"][task_id]
        if task["state"] != "pending":
            continue
        failed = _blocked_by_failed(state, task_id)
        if failed:
            task["state"] = "blocked"
            task["note"] = f"blocked by {failed}"
            EventLog(root, state["workspace_id"]).append(
                "task_blocked", actor="kernel",
                payload={"task": task_id, "reason": task["note"]}, moment=moment)
            actions.append({"task": task_id, "action": "block", "reason": task["note"]})

    for task_id in ready_tasks(state):
        task = state["tasks"][task_id]
        candidates = _eligible(state, adapters, task["requirement"])
        if not candidates:
            task["state"] = "blocked"
            task["note"] = "no capable agent"
            EventLog(root, state["workspace_id"]).append(
                "task_blocked", actor="kernel", payload={"task": task_id, "reason": task["note"]}, moment=moment)
            actions.append({"task": task_id, "action": "block", "reason": task["note"]})
            continue
        owner = candidates[0]
        adapter = find(adapters, owner)
        task.update({"owner": owner, "state": "running", "attempts": task["attempts"] + 1})
        state["agents"].setdefault(owner, {})["state"] = "active"
        result = adapter.deliver({"id": task_id, "requirement": task["requirement"],
                                  "workspace_id": state["workspace_id"]})
        status, signal = result.get("status"), result.get("signal", "")
        EventLog(root, state["workspace_id"]).append(
            "delivery", actor=owner,
            payload={"task": task_id, "status": status, "signal": signal,
                     "note": result.get("note", "")}, moment=moment)
        if status == "accepted":
            actions.append({"task": task_id, "action": "deliver", "agent": owner})
            continue
        # A bare "failed" carries no cause, so it is escalated rather than
        # silently retried; only a named signal may trigger an automatic retry.
        task["signal"] = signal or {"refused": "refusal"}.get(status, "unclassified")
        decision = diagnosis.classify(task["signal"], attempts=task["attempts"])
        if decision["action"] == "handoff":
            packet = _handoff(root, state, task, adapters, reason=task["signal"], moment=moment)
            actions.append({"task": task_id, "action": "handoff", "agent": owner,
                            "handoff": bool(packet), "reason": decision["reason"]})
        elif decision["action"] == "switch_agent":
            reason = "refusal" if task["signal"] == "refusal" else "stalled"
            packet = _handoff(root, state, task, adapters, reason=reason, moment=moment)
            actions.append({"task": task_id, "action": "switch_agent", "agent": owner,
                            "handoff": bool(packet), "reason": decision["reason"]})
        elif decision["action"] == "retry" and task["attempts"] <= diagnosis.MAX_RETRY:
            task["state"] = "pending"
            task["owner"] = ""
            actions.append({"task": task_id, "action": "retry", "agent": owner})
        else:
            task["state"] = "failed"
            actions.append({"task": task_id, "action": decision["action"], "agent": owner,
                            "reason": decision["reason"]})
    save(root, state)
    return {"status": "TICKED", "actions": actions}


def complete(root, state, *, task_id, artifact_path=None, moment=None):
    task = state["tasks"].get(task_id)
    if task is None:
        raise ValueError("unknown task")
    if task["state"] != "running":
        raise ValueError("only a running task can be completed")
    task["state"] = "done"
    if artifact_path:
        state.setdefault("archive", []).append(snapshot_mod.artifact(artifact_path))
    EventLog(root, state["workspace_id"]).append(
        "task_done", actor=task["owner"] or "kernel",
        payload={"task": task_id, "artifact": str(artifact_path) if artifact_path else ""}, moment=moment)
    save(root, state)
    return task


def ingest_external(root, state, message, *, moment=None):
    """Accept a message only when its watermark matches; otherwise quarantine."""
    claimed = (message or {}).get("workspace_id")
    if claimed != state["workspace_id"]:
        state["quarantine"].append({"at": stamp(moment), "claimed_workspace": claimed,
                                    "reason": "WORKSPACE_MISMATCH",
                                    "preview": str((message or {}).get("text", ""))[:200]})
        EventLog(root, state["workspace_id"]).append(
            "message_quarantined", actor="kernel",
            payload={"claimed_workspace": claimed}, moment=moment)
        save(root, state)
        return {"accepted": False, "reason": "WORKSPACE_MISMATCH"}
    EventLog(root, state["workspace_id"]).append(
        "message_accepted", actor=str(message.get("actor", "unknown")),
        payload={"text": str(message.get("text", ""))[:500]}, moment=moment)
    save(root, state)
    return {"accepted": True}


def report_conflict(root, state, *, task_id, claims, moment=None):
    """Open an arbitration task instead of letting two conclusions coexist."""
    if task_id not in state["tasks"]:
        raise ValueError("unknown task")
    if not isinstance(claims, (list, tuple)) or len(claims) < 2:
        raise ValueError("a conflict needs at least two claims")
    arbitration_id = f"arbitrate-{task_id}"
    state["conflicts"].append({"at": stamp(moment), "task": task_id,
                               "claims": [str(item)[:500] for item in claims]})
    if arbitration_id not in state["tasks"]:
        state["tasks"][arbitration_id] = {
            "id": arbitration_id, "requirement": "review", "deps": [],
            "state": "pending", "owner": "", "attempts": 0, "signal": "", "note": "",
            "arbitrates": task_id}
    EventLog(root, state["workspace_id"]).append(
        "conflict_opened", actor="kernel",
        payload={"task": task_id, "arbitration": arbitration_id, "claims": len(claims)}, moment=moment)
    save(root, state)
    return state["tasks"][arbitration_id]


def pause(root, state, *, reason, moment=None):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("pause reason is required")
    state["paused"] = True
    state["pause_reason"] = reason.strip()
    EventLog(root, state["workspace_id"]).append(
        "workspace_paused", actor="operator", payload={"reason": reason.strip()}, moment=moment)
    save(root, state)
    return state


def resume(root, state, *, moment=None):
    state["paused"] = False
    state.pop("pause_reason", None)
    EventLog(root, state["workspace_id"]).append("workspace_resumed", actor="operator", moment=moment)
    save(root, state)
    return state


def recover(root, workspace_id):
    """Rebuild from disk: verify the event chain and the latest snapshot."""
    state = load(root, workspace_id)
    chain = EventLog(root, workspace_id).verify()
    if not chain["valid"]:
        raise ValueError(f"EVENT_CHAIN_INVALID: {chain['reason']} at seq {chain['seq']}")
    latest = snapshot_mod.latest(root, workspace_id)
    report = snapshot_mod.verify(latest) if latest else None
    return {"workspace_id": workspace_id, "paused": state["paused"],
            "tasks": len(state["tasks"]), "events": chain["events"],
            "snapshot": report, "state": state}
