"""Deliver federation tasks to real desktop Agent sessions.

The kernel schedules and the commander types.  This adapter is the only place
those two meet, and the split is deliberate:

* :meth:`CommanderAdapter.deliver` **queues** an order.  It never opens a
  window, never focuses a composer and never waits on a UI.  A workspace lock is
  held while the kernel ticks, so doing UI work there would serialise every
  agent behind one 75-second read-back timeout.
* :meth:`CommanderAdapter.heartbeat` reports progress from the durable order
  record, not from "the process is alive".  A chat client can be running,
  focused and completely idle; that is not progress.
* ``coordination.commander.tick`` then pumps the queue into real sessions
  outside the lock.  The hands stay where they already work.

Mapping is one order per task.  ``deliver`` is idempotent: a second call for a
task that already has a live order returns ``accepted`` without depositing a
duplicate, because re-typing the same instruction into a live conversation is
how an agent gets asked to do the same thing twice.
"""
from __future__ import annotations

from datetime import datetime, timezone

#: Order states that mean "this order will never move again".  A task with one
#: of these may be handed off; a task whose order is merely deferred may not.
TERMINAL_ORDER_STATES = frozenset({"AGENT_ACK", "FAILED", "CEASED", "RETIRED"})
#: States that prove input reached the conversation without proving the Agent
#: accepted the work.  They are progress, not completion.
DELIVERED_ORDER_STATES = frozenset({"INPUT_SUBMITTED", "MESSAGE_VISIBLE"})


def _moment(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class CommanderAdapter:
    """A federation ``Adapter`` backed by the durable desktop commander queue.

    ``capabilities`` must be declared, not inferred: the kernel picks an owner
    by capability, so an adapter that guesses would silently take work it cannot
    do.  The default is the capability every desktop agent can be asked for.
    """

    def __init__(self, agent, *, coord_root, sender="kernel", capabilities=("execute",),
                 transport="auto", allow_launch=False, workspace_id="",
                 expires_minutes=120, urgent_interject=False, assignment=None):
        if not isinstance(agent, str) or not agent.strip():
            raise ValueError("agent is required")
        if not isinstance(coord_root, str) or not coord_root.strip():
            raise ValueError("coord_root is required")
        self.agent = agent.strip()
        self.coord_root = coord_root
        self.sender = sender
        self._capabilities = {str(item) for item in capabilities}
        if not self._capabilities:
            raise ValueError("an adapter must advertise at least one capability")
        self.transport = transport
        self.allow_launch = bool(allow_launch)
        self.workspace_id = workspace_id
        self.expires_minutes = int(expires_minutes)
        self.urgent_interject = bool(urgent_interject)
        #: ``assignment`` renders the human-facing instruction.  Injecting it
        #: keeps this module free of a hard dependency on the mailbox format.
        self._assignment = assignment

    # -- capability -------------------------------------------------------
    def capabilities(self):
        return set(self._capabilities)

    # -- durable order view ----------------------------------------------
    def _orders(self):
        from agent_evolution_protocol.coordination import commander
        return commander._orders(self.coord_root)

    def _orders_for_task(self, task_id):
        return [order for order in self._orders()
                if order.get("target") == self.agent
                and str(order.get("task", "")) == str(task_id)]

    def order_for_task(self, task_id):
        """The newest order this adapter deposited for ``task_id``, or None."""
        rows = self._orders_for_task(task_id)
        if not rows:
            return None
        return max(rows, key=lambda row: str(row.get("created_at", "")))

    def live_order(self, task_id):
        """The order that still owns ``task_id``, or None when it is finished."""
        order = self.order_for_task(task_id)
        if order is None or order.get("state") in TERMINAL_ORDER_STATES:
            return None
        return order

    # -- adapter protocol -------------------------------------------------
    def heartbeat(self, now):
        """Report the newest durable progress for this agent.

        ``last_progress_at`` is the latest of: order deposit, any recorded
        attempt, and the order's own ``updated_at``.  Using the deposit time
        matters -- a task that was just queued has no attempt yet, and treating
        that as "no heartbeat" would hand it off in the same tick it was sent.
        """
        orders = [order for order in self._orders() if order.get("target") == self.agent]
        if not orders:
            return {"agent": self.agent, "alive": True, "last_progress_at": None,
                    "now": now, "orders": 0,
                    "note": "no order yet; absence of work is not a stalled agent"}
        marks = []
        for order in orders:
            for value in (order.get("created_at"), order.get("updated_at")):
                moment = _moment(value)
                if moment is not None:
                    marks.append(moment)
            for attempt in order.get("attempts") or []:
                moment = _moment(attempt.get("at"))
                if moment is not None:
                    marks.append(moment)
        newest = max(marks) if marks else None
        return {"agent": self.agent, "alive": True,
                "last_progress_at": newest.isoformat() if newest else None,
                "now": now, "orders": len(orders)}

    def deliver(self, task):
        """Queue one assignment; never touch the UI from here."""
        from agent_evolution_protocol.coordination import commander

        if not isinstance(task, dict) or not task.get("id"):
            return {"status": "refused", "signal": "invalid_task",
                    "note": "task id is required"}
        task_id = str(task["id"])
        existing = self.live_order(task_id)
        if existing is not None:
            return {"status": "accepted", "signal": "",
                    "note": f"already queued as {existing['id']} ({existing.get('state')})",
                    "order_id": existing["id"]}
        render = self._assignment or _default_assignment
        try:
            text = render(agent=self.agent, task=task, coord_root=self.coord_root,
                          workspace_id=task.get("workspace_id") or self.workspace_id,
                          sender=self.sender)
        except (TypeError, ValueError) as exc:
            return {"status": "refused", "signal": "invalid_assignment",
                    "note": str(exc)}
        try:
            order = commander.submit_order(
                self.coord_root, target=self.agent, task=task_id, text=text,
                sender=self.sender, priority=str(task.get("priority", "normal")),
                allow_launch=self.allow_launch, transport=self.transport,
                expires_minutes=self.expires_minutes,
                workspace_id=task.get("workspace_id") or self.workspace_id,
                urgent_interject=self.urgent_interject)
        except (OSError, ValueError) as exc:
            return {"status": "failed", "signal": "order_rejected", "note": str(exc)}
        return {"status": "accepted", "signal": "",
                "note": f"queued as {order['id']}", "order_id": order["id"],
                "event_id": order["event_id"]}

    def observe(self):
        """Summarise this agent's orders without pretending to read a screen."""
        rows = [order for order in self._orders() if order.get("target") == self.agent]
        counts = {}
        for order in rows:
            state = str(order.get("state", "UNKNOWN"))
            counts[state] = counts.get(state, 0) + 1
        return {"agent": self.agent, "orders": len(rows), "states": counts,
                "acked": counts.get("AGENT_ACK", 0),
                "pending": sum(count for state, count in counts.items()
                               if state not in TERMINAL_ORDER_STATES)}

    # -- closed-loop helpers ---------------------------------------------
    def acknowledged(self, task_id):
        order = self.order_for_task(task_id)
        return bool(order and order.get("state") == "AGENT_ACK")

    def failure_signal(self, task_id):
        """Translate a terminal order into a diagnosis signal, or ``""``.

        Only a *terminal* order produces a signal.  A deferred order is the
        client being busy, which the commander already retries on readiness.
        """
        order = self.order_for_task(task_id)
        if order is None:
            return ""
        state = str(order.get("state", ""))
        if state == "CEASED":
            return "adapter_crashed"
        if state != "FAILED":
            return ""
        note = str(order.get("note", "")).upper()
        if "EXPIRED" in note:
            return "token_exhausted"
        attempts = order.get("attempts") or []
        last = (attempts[-1].get("receipt") or {}) if attempts else {}
        for key in ("signal", "status", "error", "state"):
            value = str(last.get(key, "")).strip()
            if value:
                return value
        return "unclassified"


def _default_assignment(*, agent, task, coord_root, workspace_id, sender):
    """Fallback instruction used when no renderer is injected.

    Kept deliberately minimal: the real text lives in ``federation.assignment``
    so the mailbox format can evolve without touching the adapter.
    """
    requirement = str(task.get("requirement", "")).strip()
    if not requirement:
        raise ValueError("task requirement is required")
    return (f"[AEP:{task['id']}] {requirement}\n"
            f"workspace={workspace_id or '-'} sender={sender} root={coord_root}")
