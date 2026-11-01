"""The heartbeat that lets one input drive every agent without a human relay.

A workspace that only schedules is a to-do list.  Something has to keep asking
"is anyone on this, and did it move?" -- otherwise the operator becomes the
message bus, which is exactly the complaint this module exists to remove.

One :func:`step` does four things, in this order and no other:

1. **schedule** -- ``engine.tick`` picks eligible agents and queues assignments
   (the heart);
2. **deliver** -- ``commander.tick`` types the queued orders into real sessions
   (the hands);
3. **read back** -- each running task's assignee is asked what it reported
   through ``coord-task-state``, and only an explicit phase counts;
4. **repair** -- a task whose order died, or whose owner reported blocked, is
   handed to the next capable agent through the same decision table.

Step 3 is why this is not a cron job with a nice name.  The loop is closed by
evidence the *agent* wrote, not by the supervisor assuming that silence means
success.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from agent_evolution_protocol.federation import assignment, engine
from agent_evolution_protocol.federation.commander_adapter import CommanderAdapter

#: Signals a supervisor may report on behalf of an executor.  Kept small: each
#: one must be provable from a durable record, never from a screen glance.
OBSERVED_SIGNALS = frozenset({"adapter_crashed", "adapter_timeout", "refusal"})


def build_adapters(*, coord_root, agents, capabilities=None, sender="kernel",
                   transport="auto", allow_launch=False, workspace_id="",
                   expires_minutes=120, urgent_interject=False):
    """One adapter per agent, with declared capabilities.

    ``capabilities`` maps an agent to its capability list.  An agent with no
    declared capability still gets ``execute`` -- the one thing a desktop agent
    can always be asked to do -- but anything narrower must be stated, because
    the scheduler trusts this map when it picks an owner.
    """
    if not isinstance(coord_root, str) or not coord_root.strip():
        raise ValueError("coord_root is required")
    declared = dict(capabilities or {})
    adapters = []
    for agent in agents:
        if not isinstance(agent, str) or not agent.strip():
            raise ValueError("agent names must be non-empty strings")
        caps = declared.get(agent) or ["execute"]
        adapters.append(CommanderAdapter(
            agent, coord_root=coord_root, sender=sender, capabilities=caps,
            transport=transport, allow_launch=allow_launch,
            workspace_id=workspace_id, expires_minutes=expires_minutes,
            urgent_interject=urgent_interject,
            assignment=assignment.render))
    if not adapters:
        raise ValueError("at least one agent is required")
    return adapters


def step(root, coord_root, workspace_id, adapters, *, now=None,
         heartbeat_timeout=900, dispatch=True, perceive_first=False,
         moment=None, hold_limit_seconds=None):
    """Advance one bounded step.  Returns what changed and why.

    ``hold_limit_seconds`` does not advance anything when it is exceeded.  A hold
    that turns into progress on a timer is not "no ACK, no progress" any more; it
    is "no ACK, progress after N minutes", which is the behaviour the guarantee
    exists to rule out.  Crossing the limit only makes the hold *loud* -- the
    receipt says how long it has waited and that the limit is past, so a person
    can decide.  Ending the wait takes ``commander.abandon`` and a signature.
    """
    from agent_evolution_protocol.coordination import commander

    now = now or datetime.now(timezone.utc)
    state = engine.load(root, workspace_id)
    actions = []

    # 1. settle first: a task that finished last tick makes its downstream node
    #    ready *now*, so it can be scheduled in this same step instead of
    #    waiting a whole heartbeat interval for no reason.
    closed, held = _settle(root, coord_root, workspace_id, adapters, moment=moment,
                           actions=actions, now=now,
                           hold_limit_seconds=hold_limit_seconds)

    # 2. heart: schedule and queue
    state = engine.load(root, workspace_id)
    ticked = engine.tick(root, state, adapters, now=now,
                         heartbeat_timeout=heartbeat_timeout, moment=moment)
    actions.extend(ticked.get("actions") or [])

    # 3. lease the write scope before anything is typed into a session: work
    #    that cannot be written must not be dispatched.
    state = engine.load(root, workspace_id)
    claims = _claim_scopes(coord_root, state, root, moment=moment)

    # 4. hands: type the queued orders into real sessions
    sent = {}
    if dispatch:
        try:
            sent = commander.tick(coord_root, perceive_first=perceive_first)
        except (OSError, ValueError) as exc:
            sent = {"error": str(exc)}

    state = engine.load(root, workspace_id)
    return {"at": now.isoformat(), "workspace": workspace_id,
            "scheduled": ticked.get("actions") or [], "sent": sent,
            "claims": claims, "closed": closed, "held": held,
            "tasks": {task_id: {"state": row["state"], "owner": row["owner"],
                                "attempts": row["attempts"]}
                      for task_id, row in sorted(state["tasks"].items())}}


def _settle(root, coord_root, workspace_id, adapters, *, moment, actions,
            now=None, hold_limit_seconds=None):
    """Close every running task whose assignee produced evidence, or died."""
    from agent_evolution_protocol.coordination import commander

    # Refresh the ACK state before judging anything.  `commander.tick` does this
    # too, but it runs after this function, so without the refresh every verdict
    # here is one step stale -- and a task would be held for an ACK that already
    # arrived.  Reading a transcript and upgrading a receipt never types.
    try:
        commander.check_acks(Path(coord_root))
    except (OSError, ValueError):
        pass
    state = engine.load(root, workspace_id)
    closed = []
    held = []
    for task_id in sorted(state["tasks"]):
        task = state["tasks"][task_id]
        if task["state"] != "running" or not task["owner"]:
            continue
        adapter = next((item for item in adapters if item.agent == task["owner"]), None)
        if adapter is None:
            continue
        report = assignment.complete_evidence(coord_root, task_id=task_id, agent=task["owner"])
        if report.get("done"):
            blocked = _ack_blocks(adapter, task_id, now=now,
                                  hold_limit_seconds=hold_limit_seconds)
            if blocked is not None and blocked.get("verdict") == "abandon":
                # A person ended the wait.  The task becomes blocked, not done:
                # nothing was proven about the work, only about the waiting.
                engine.abandon(root, state, task_id=task_id,
                               reason=blocked.get("reason_text") or "order abandoned",
                               actor=blocked.get("by") or "unknown", moment=moment)
                closed.append({"task": task_id, "action": "abandoned",
                               "by": blocked.get("by", "")})
                continue
            if blocked is not None:
                held.append({"task": task_id, "action": "hold_unacked", **blocked})
                continue
            artifact = _first_existing(report.get("evidence") or [])
            engine.complete(root, state, task_id=task_id, artifact_path=artifact, moment=moment)
            closed.append({"task": task_id, "action": "complete", "artifact": str(artifact or "")})
            continue
        if report.get("blocked"):
            signal = "refusal"
            if report.get("blocked_reason"):
                task["note"] = str(report["blocked_reason"])[:200]
            engine.fail_task(root, state, adapters, task_id=task_id, signal=signal,
                             moment=moment, actions=actions)
            closed.append({"task": task_id, "action": "blocked", "signal": signal})
            continue
        # An order that reached a terminal state without the agent reporting
        # progress is a dead delivery, not a slow one.
        signal = adapter.failure_signal(task_id)
        if signal and signal in OBSERVED_SIGNALS:
            engine.fail_task(root, state, adapters, task_id=task_id, signal=signal,
                             moment=moment, actions=actions)
            closed.append({"task": task_id, "action": "fail", "signal": signal})
    return closed, held


def _seconds_since(value, now):
    """Age of an ISO timestamp in whole seconds, or ``None`` if unreadable."""
    if not value or now is None:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0, int((now - moment).total_seconds()))


def _ack_blocks(adapter, task_id, *, now=None, hold_limit_seconds=None):
    """Why this completion may not advance the DAG, or ``None`` when it may.

    The rule is "no ACK, no progress" -- but only for orders that were actually
    sent.  An order that never reached a conversation has nothing to acknowledge,
    and holding it would be waiting for a reply to a message nobody delivered;
    the agent's own written evidence is the only signal that can exist there.

    An adapter with no way to report an ACK is not held to one.  Requiring
    evidence an adapter cannot produce is not rigour, it is a deadlock with good
    intentions.

    Two different answers come back, and they are not interchangeable.  A hold
    means "keep waiting" and is now reported with its age, so an unbounded wait
    is visible instead of silent.  An abandonment means a person ended the wait,
    and it resolves to ``blocked`` -- never to a completion.
    """
    from agent_evolution_protocol.coordination import ack_protocol

    probe = getattr(adapter, "ack_verdict", None)
    if not callable(probe):
        return None
    try:
        value = probe(task_id)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    signoff = value.get("abandoned")
    if isinstance(signoff, dict):
        # Checked before "was it sent": an abandonment is a decision about the
        # wait, and it blocks the task whether or not anything was delivered.
        return {"verdict": "abandon", "reason": "abandoned", "state": value.get("state"),
                "by": signoff.get("by", ""), "at": signoff.get("at", ""),
                "reason_text": signoff.get("reason", "")}
    if not value.get("sent"):
        return None
    if ack_protocol.may_advance(value):
        return None
    waited = _seconds_since(value.get("sent_at"), now)
    return {"verdict": "hold",
            "reason": value.get("reason") or "no_ack",
            "state": value.get("state"),
            "event_id": value.get("event_id", ""),
            "sent_at": value.get("sent_at", ""),
            "unacked_for_seconds": waited,
            "limit_seconds": hold_limit_seconds,
            # Over the limit changes nothing except that a person is now told to
            # look.  It is deliberately not a trigger to advance.
            "over_limit": (hold_limit_seconds is not None and waited is not None
                           and waited >= hold_limit_seconds)}


def _claim_scopes(coord_root, state, root, *, moment=None):
    """Take the lease for work that just started, or refuse to start it.

    A task delivered by ``engine.tick`` has an owner but no claim, because the
    scheduler knows nothing about files.  Claiming at start time -- not at plan
    time -- keeps a long DAG from reserving half the repository before anyone
    has written a line.
    """
    from agent_evolution_protocol.coordination import claim_conflicts

    results = []
    for task_id in sorted(state["tasks"]):
        task = state["tasks"][task_id]
        if task["state"] != "running" or not task["owner"]:
            continue
        paths = [str(item) for item in (task.get("paths") or []) if str(item).strip()]
        if not paths:
            continue
        if (Path(coord_root) / "claims" / task_id).exists():
            continue
        try:
            verdict = claim_conflicts.claim(coord_root, agent=task["owner"], task=task_id,
                                           paths=paths)
        except (OSError, ValueError) as exc:
            verdict = {"claimed": False, "reason": f"claim_error: {exc}"}
        results.append({"task": task_id, "claimed": bool(verdict.get("claimed")),
                        "reason": verdict.get("reason", "")})
        if not verdict.get("claimed"):
            task["state"] = "blocked"
            task["note"] = f"scope_conflict: {verdict.get('reason', '')}"
            engine.EventLog(root, state["workspace_id"]).append(
                "task_blocked", actor="kernel",
                payload={"task": task_id, "reason": task["note"]}, moment=moment)
    if results:
        engine.save(root, state)
    return results


def _first_existing(paths):
    for raw in paths:
        candidate = Path(str(raw))
        if candidate.is_file():
            return candidate
    return None


def serve(root, coord_root, workspace_id, adapters, *, interval=30.0, max_ticks=0,
          heartbeat_timeout=900, dispatch=True, perceive_first=False, stream=None,
          sleep=time.sleep):
    """Run :func:`step` until ``max_ticks`` or until every task is settled.

    Returns the number of steps taken.  A caller that wants one shot should call
    :func:`step` directly; this is the unattended mode.
    """
    if interval < 1.0:
        raise ValueError("interval below one second is a busy loop, not a heartbeat")
    ticks = 0
    while True:
        result = step(root, coord_root, workspace_id, adapters,
                      heartbeat_timeout=heartbeat_timeout, dispatch=dispatch,
                      perceive_first=perceive_first)
        ticks += 1
        if stream is not None:
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            stream.flush()
        if max_ticks and ticks >= max_ticks:
            return ticks
        if _settled(result):
            return ticks
        sleep(interval)


def _settled(result):
    tasks = result.get("tasks") or {}
    return bool(tasks) and all(row["state"] in {"done", "failed", "blocked"}
                               for row in tasks.values())
