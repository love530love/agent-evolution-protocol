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

from agent_evolution_protocol.coordination import hold_alerts
from agent_evolution_protocol.federation import assignment, engine
from agent_evolution_protocol.federation.commander_adapter import CommanderAdapter

#: Signals a supervisor may report on behalf of an executor.  Kept small: each
#: one must be provable from a durable record, never from a screen glance.
OBSERVED_SIGNALS = frozenset({"adapter_crashed", "adapter_timeout", "refusal"})

#: What an unacknowledged order may do to the DAG.  ``"record"`` advances and
#: writes the verdict into the receipt; ``"enforce"`` holds and does not advance.
#: Two values only: a third would be a policy expressed as a number of minutes,
#: which is the timer this protocol exists to avoid.
ACK_POLICIES = ("record", "enforce")

#: Default is the non-blocking one.  Chosen because a gate with no exception is
#: not strictness, it is an outage waiting for the first adapter that cannot
#: report an ACK -- and the fact survives either way in ``closed[].ack``.
DEFAULT_ACK_POLICY = "record"


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
         moment=None, hold_limit_seconds=3600, ack_policy=DEFAULT_ACK_POLICY):
    """Advance one bounded step.  Returns what changed and why.

    ``ack_policy`` decides what an unacknowledged order does to the DAG, and it
    has exactly two answers.

    ``"record"`` (the default since v0.6.15) **advances anyway** and writes the
    unacknowledged verdict into the completion receipt.  The rule stopped being
    an advance condition because a hard gate had no exception: an adapter that
    cannot report an ACK, a session that was closed by a person, a client that
    never shows the reply -- none of those mean the work is missing, and all of
    them used to pin the task in ``running`` forever.  What is *not* given up is
    the record: the completed task carries ``ack.verdict == "hold"``, so "this
    advanced without an acknowledgement" is a field, not an absence.

    ``"enforce"`` is the v0.6.14 behaviour: hold, do not advance.  Kept because
    a deployment where a missing ACK really does mean "do not proceed" needs to
    be able to say so, and because deleting the strict path would make the
    relaxation impossible to measure against.

    ``hold_limit_seconds`` does not advance anything under ``"enforce"``.  A hold
    that turns into progress on a timer is not "no ACK, no progress" any more; it
    is "no ACK, progress after N minutes", which is the behaviour the guarantee
    exists to rule out.  Crossing the limit makes the hold *loud*: the receipt
    says how long it has waited, and an alert lands in the mailbox a person
    already reads.  Ending the wait takes ``commander.abandon`` and a signature.

    Under ``"record"`` there is no hold left to shout about, so **no alert is
    emitted** -- the task is not stuck, and an alert that asks a person to
    abandon a finished task would be worse than silence.  That is a real loss
    of visibility compared with ``"enforce"``, and it is the price of the
    relaxation rather than a detail.

    The default 3600 is a **policy, not a measurement** -- nothing here derives it
    from observed latencies.  It is short enough that a person hears about a hold
    while the decision is still theirs to make, and it is overridable per call.
    Pass ``None`` to disable alerting entirely; that is the pre-v0.6.14 behaviour
    and it is available on purpose, because a deployment may prefer to watch
    ``held`` itself.
    """
    from agent_evolution_protocol.coordination import commander

    if ack_policy not in ACK_POLICIES:
        raise ValueError(f"ack_policy must be one of {ACK_POLICIES}, got {ack_policy!r}")

    now = now or datetime.now(timezone.utc)
    state = engine.load(root, workspace_id)
    actions = []

    # 1. settle first: a task that finished last tick makes its downstream node
    #    ready *now*, so it can be scheduled in this same step instead of
    #    waiting a whole heartbeat interval for no reason.
    closed, held, released = _settle(root, coord_root, workspace_id, adapters, moment=moment,
                                     actions=actions, now=now,
                                     hold_limit_seconds=hold_limit_seconds,
                                     ack_policy=ack_policy)

    # 2. tell a person about a hold that has outlived its limit.  This is a
    #    message, never a transition: `held` rows are still held afterwards.
    #    A failure here must not stop the heartbeat -- an alerting path that can
    #    take the loop down is worse than the silence it replaced.
    alerts = {}
    try:
        alerts = hold_alerts.emit(coord_root, held)
    except (OSError, ValueError, TypeError) as exc:
        alerts = {"error": str(exc)}

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
            # Tasks that completed without an acknowledgement.  Empty under
            # ``enforce`` by construction, because those never leave ``held``.
            "released": released, "ack_policy": ack_policy, "alerts": alerts,
            "tasks": {task_id: {"state": row["state"], "owner": row["owner"],
                                "attempts": row["attempts"]}
                      for task_id, row in sorted(state["tasks"].items())}}


def _result_blocks(task_id, report, coord_root, task):
    """Whether the agent's claim about its own output contradicts the transcript.

    Only ``match is False`` blocks.  ``None`` -- "the transcript carries nothing
    comparable" -- does not, and that asymmetry is the whole design: a module
    that returned ``None`` for every agent that never names a file path would
    deadlock the DAG on its first task.  The verdict is still recorded on the
    completion, so "we did not verify this" is a field in the receipt instead of
    an assumption nobody wrote down.

    The session guard is wired here, since v0.6.15.  The transcript read is
    per-agent, but an agent keeps talking across tasks and windows, so comparing
    against all of it lets a path from another conversation count as this task's
    output.  The anchor is the order's ``session_id`` -- the conversation the
    work was actually dispatched into -- looked up from the order because this
    function is handed the task.  When the order has no ``session_id`` the guard
    cannot run, and ``observe`` reports ``session_checked: False`` rather than
    comparing everything and calling it verified.  What the guard cannot do is
    make a missing anchor safe: an unanchored comparison still returns ``None``
    for "nothing comparable", never ``True``.
    """
    from agent_evolution_protocol.coordination import commander, result_readback, transcript_bus

    session_id = ""
    own_text = ""
    task_orders = [order for order in commander._orders(coord_root)
                   if str(order.get("task") or "") == task_id]
    if task_orders:
        # _orders() is sorted by order filename/ID; use the latest delivery so
        # the session and own-text guard describe the message actually sent on
        # the current route, not a predecessor's older request.
        order = task_orders[-1]
        session_id = str(order.get("session_id") or "")
        own_text = str(order.get("text") or "")
    events = transcript_bus.read_events(coord_root, agent=task.get("owner") or "", limit=500)
    claim = result_readback.claim({**report, "task": task_id})
    observed = result_readback.observe(events, session_id=session_id, own_text=own_text)
    value = result_readback.judge(claim, observed)
    if value.get("match") is not False:
        return None, value
    return {"reason": value.get("reason") or "result_mismatch",
            "match": False, "claimed": value.get("claimed"),
            "seen": value.get("seen")}, value


def _settle(root, coord_root, workspace_id, adapters, *, moment, actions,
            now=None, hold_limit_seconds=None, ack_policy=DEFAULT_ACK_POLICY):
    """Close every running task whose assignee produced evidence, or died.

    Returns ``(closed, held, released)``.  ``released`` exists because the two
    old buckets could not express the third outcome: a task that completed
    *without* an acknowledgement.  Filing it under ``closed`` alone would make it
    indistinguishable from a clean completion, and filing it under ``held`` would
    be a lie -- it is not held, it advanced.
    """
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
    released = []
    for task_id in sorted(state["tasks"]):
        task = state["tasks"][task_id]
        if task["state"] != "running" or not task["owner"]:
            continue
        adapter = next((item for item in adapters if item.agent == task["owner"]), None)
        if adapter is None:
            continue
        report = assignment.complete_evidence(coord_root, task_id=task_id, agent=task["owner"])
        if report.get("done"):
            verdict = _ack_blocks(adapter, task_id, now=now,
                                  hold_limit_seconds=hold_limit_seconds)
            if verdict.get("verdict") == "abandon":
                # A person ended the wait.  The task becomes blocked, not done:
                # nothing was proven about the work, only about the waiting.
                # This is not "no ACK", so ``ack_policy`` does not reach it --
                # relaxing a missing acknowledgement must never relax a human
                # signature, which is the opposite kind of evidence.
                engine.abandon(root, state, task_id=task_id,
                               reason=verdict.get("reason_text") or "order abandoned",
                               actor=verdict.get("by") or "unknown", moment=moment)
                closed.append({"task": task_id, "action": "abandoned",
                               "by": verdict.get("by", ""), "ack": verdict})
                continue
            if verdict.get("verdict") == "hold" and ack_policy == "enforce":
                held.append({"task": task_id, "action": "hold_unacked", **verdict})
                continue
            mismatch, result_verdict = _result_blocks(task_id, report, coord_root, task)
            if mismatch is not None:
                # The agent named an artifact and the transcript names a
                # different one.  That is a contradiction, not a missing detail,
                # so it does not complete and it does not wait for an ACK either.
                # Deliberately outside ``ack_policy``: a proven contradiction is
                # evidence against the work, where a missing ACK is only absence
                # of evidence about the delivery.
                held.append({"task": task_id, "action": "hold_result_mismatch", **mismatch})
                continue
            if verdict.get("verdict") == "hold":
                # ``record``: advance, but never silently.  Appended only after
                # the contradiction gate, because a row here means "this
                # advanced" -- and a task held for a mismatched artifact did not.
                # Writing it earlier made ``released`` claim an advance that
                # never happened, which is the same defect as the ones this
                # release is about, committed by me while writing about them.
                released.append({"task": task_id, "action": "released_unacked", **verdict})
            artifact = _first_existing(report.get("evidence") or [])
            engine.complete(root, state, task_id=task_id, artifact_path=artifact, moment=moment)
            closed.append({"task": task_id, "action": "complete", "artifact": str(artifact or ""),
                           "result": {"match": result_verdict.get("match"),
                                      "reason": result_verdict.get("reason", ""),
                                      # Whether the comparison was anchored to the
                                      # dispatched conversation.  Kept in the receipt
                                      # because otherwise "we wired the session guard"
                                      # and "we forgot to" are indistinguishable from
                                      # outside, which is how the previous release
                                      # shipped a guard that was never connected.
                                      "session_checked": bool(
                                          (result_verdict.get("evidence") or {}).get("session_checked")),
                                      "session_unknown": bool(
                                          (result_verdict.get("evidence") or {}).get("session_unknown"))},
                           # The ACK verdict rides on every completion, including
                           # the clean ones: ``reason`` tells "acked" from
                           # "not_sent" from "no_ack_support", which the old
                           # ``None`` collapsed into one indistinguishable value.
                           "ack": verdict})
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
    return closed, held, released


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
    """What the acknowledgement record says about this completion.

    Always returns a dict; ``verdict`` is one of ``"clear"``, ``"hold"`` or
    ``"abandon"``.  It used to return ``None`` for "may advance", which threw
    away the difference between three very different facts -- the order was
    acknowledged, the order was never sent, and this adapter cannot report an
    ACK at all.  Under ``ack_policy="record"`` those all complete anyway, so the
    only thing that distinguishes them is the receipt, and ``None`` cannot carry
    it.  Naming them costs one string and stops "we verified the agent replied"
    from being indistinguishable from "nobody could have replied".

    The rule is "no ACK, no progress" -- but only for orders that were actually
    sent.  An order that never reached a conversation has nothing to acknowledge,
    and holding it would be waiting for a reply to a message nobody delivered;
    the agent's own written evidence is the only signal that can exist there.

    An adapter with no way to report an ACK is not held to one.  Requiring
    evidence an adapter cannot produce is not rigour, it is a deadlock with good
    intentions.

    Three different answers come back, and they are not interchangeable.  A
    ``hold`` means "sent, and no acknowledgement is on record".  An ``abandon``
    means a person ended the wait, and it resolves to ``blocked`` -- never to a
    completion, under either policy.
    """
    from agent_evolution_protocol.coordination import ack_protocol

    probe = getattr(adapter, "ack_verdict", None)
    if not callable(probe):
        return {"verdict": "clear", "reason": "no_ack_support"}
    try:
        value = probe(task_id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Unreadable is not the same as absent, but neither can hold a task:
        # an order we cannot read a verdict for is one we cannot prove anything
        # about, and the reason says which it was.
        return {"verdict": "clear", "reason": f"ack_unreadable: {type(exc).__name__}"}
    if not isinstance(value, dict):
        return {"verdict": "clear", "reason": "no_ack_record"}
    signoff = value.get("abandoned")
    if isinstance(signoff, dict):
        # Checked before "was it sent": an abandonment is a decision about the
        # wait, and it blocks the task whether or not anything was delivered.
        return {"verdict": "abandon", "reason": "abandoned", "state": value.get("state"),
                "by": signoff.get("by", ""), "at": signoff.get("at", ""),
                "reason_text": signoff.get("reason", "")}
    if not value.get("sent"):
        return {"verdict": "clear", "reason": "not_sent", "state": value.get("state")}
    if ack_protocol.may_advance(value):
        return {"verdict": "clear", "reason": "acked", "state": value.get("state"),
                "sent_at": value.get("sent_at", "")}
    waited = _seconds_since(value.get("sent_at"), now)
    return {"verdict": "hold",
            "reason": value.get("reason") or "no_ack",
            "state": value.get("state"),
            "event_id": value.get("event_id", ""),
            # The order id, because the held row's ``task`` is the *workspace*
            # task id (``execute``) and the order's ``task`` field is whatever
            # the adapter was told to send -- different namespaces.  An alerting
            # layer that had to guess which order a hold is about would name the
            # wrong agent, or none.
            "order": value.get("order_id", ""),
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
          sleep=time.sleep, hold_limit_seconds=3600, ack_policy=DEFAULT_ACK_POLICY):
    """Run :func:`step` until ``max_ticks`` or until every task is settled.

    Returns the number of steps taken.  A caller that wants one shot should call
    :func:`step` directly; this is the unattended mode.  ``ack_policy`` and
    ``hold_limit_seconds`` are passed through unchanged so that unattended and
    attended runs cannot silently disagree about whether a missing ACK blocks --
    the loop is where the difference would be hardest to notice.
    """
    if interval < 1.0:
        raise ValueError("interval below one second is a busy loop, not a heartbeat")
    if ack_policy not in ACK_POLICIES:
        raise ValueError(f"ack_policy must be one of {ACK_POLICIES}, got {ack_policy!r}")
    ticks = 0
    while True:
        result = step(root, coord_root, workspace_id, adapters,
                      heartbeat_timeout=heartbeat_timeout, dispatch=dispatch,
                      perceive_first=perceive_first,
                      hold_limit_seconds=hold_limit_seconds, ack_policy=ack_policy)
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
