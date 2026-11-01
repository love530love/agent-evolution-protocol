"""Deliver an over-limit hold to a person, once, through the mailbox that exists.

``supervisor.step`` reports a hold when an order was sent and the agent never
acknowledged it.  Since v0.6.13 that row carries ``unacked_for_seconds``,
``sent_at``, ``limit_seconds`` and ``over_limit`` -- but only in the *return
value* of one call.  An unattended loop therefore holds a task past its limit
forever and nobody is told.  This module is the exit: it takes the ``held`` list
and leaves a message where a human already looks.

**Which channel, and why not a new one.**  The repository already has a message
standard: ``coord-send`` writes ``coordination/messages/<to>/<id>.json`` with
``{id, at, sender, to, topic, kind, text}``, ``coord-inbox``/``coord-digest``
read it, and ``coordination/acks/<to>/<id>.json`` tracks whether it was read.
``kind`` already has ``warning``.  An alert *is* a warning addressed to the
party that asked for the work, so it goes into that same envelope at that same
path -- nothing here defines a second message format, a second directory or a
second unread counter.  The recipient is the order's own ``sender``: the party
that dispatched the work is a recorded fact, where "operator" would be an inbox
nobody agreed to watch.  ``recipient=`` overrides it when a deployment wants one
fixed address.

Two existing streams were considered and rejected, for the record:

* ``federation.store.EventLog`` (``workspaces/<id>/events.jsonl``) is a
  hash-chained, workspace-scoped record under the *kernel* root.  This module is
  handed ``coord_root`` and nothing else, and an event log is a history to
  verify, not an inbox anyone reads.  Appending to it would also force a
  ``workspace_id`` the caller does not pass.
* ``coordination/presence/notices`` is a presence-transition stream
  (``transition``/``previous``/``state`` per agent).  A hold is not a presence
  change, and writing one there would make ``transition`` mean two things.

**Once per hold.**  The message id is derived from the hold's identity --
``task | event_id | sent_at`` -- so the mailbox file *is* the durable marker: a
second tick computes the same id, finds the file and stays quiet.  No ledger, no
counter, no new store; the marker survives a crash because it is the alert
itself.  A hold that clears and later returns does so through a new send with a
new ``sent_at`` (clearing needs an ACK or a signature), so it hashes to a new id
and alerts again.

**An alert is a message, not a transition.**  Nothing here writes to the task,
the order or the workspace: a hold must still hold, and only ``commander.abandon``
with a signature may end the wait.  What this module cannot catch: a hold whose
order never produced a parseable ``sent_at`` (no honest wait time and no stable
id, so it is skipped rather than shouted about), a task stuck for any reason
other than an unacked order (no ``held`` row exists for it), and an operator who
does not read the inbox -- this closes the "nobody was told" gap, not the "nobody
looked" one.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

#: ``kind`` values the mailbox already understands; an alert is a warning.
ALERT_KIND = "warning"
#: Who the alert is from.  A system identity, never an agent pretending to be one.
ALERT_SENDER = "supervisor"
#: Where an alert goes when the order record cannot name its sender.  A message
#: to a guessed recipient is still better than a hold nobody hears about.
DEFAULT_RECIPIENT = "kernel"
#: Agent names the mailbox accepts, same rule as ``coord-send``.
SAFE_AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def emit(coord_root, held_rows, *, now=None, recipient="", sender=ALERT_SENDER):
    """Leave one mailbox message per over-limit hold that has not been announced.

    Callable with just ``(coord_root, held_rows)``; the supervisor passes its
    ``held`` list and learns nothing about desktops or mailboxes.  Returns what
    happened, so the caller can put it in its own receipt::

        {"count": 1,
         "alerts":     [{"task", "agent", "order", "event_id", "message_id", "to"}],
         "suppressed": [{"task", "message_id", "reason": "already_alerted"}],
         "waiting":    [{"task", "waited_seconds", "limit_seconds"}],
         "skipped":    [{"task", "reason"}]}

    ``waiting`` rows are holds that have not crossed the limit: a limit that
    makes noise before it is reached is noise, not a warning.  ``skipped`` rows
    are malformed input; they are counted and dropped rather than raised,
    because an alerting path that can kill the heartbeat is worse than no alert.
    """
    if not isinstance(coord_root, (str, Path)) or not str(coord_root).strip():
        raise ValueError("coord_root is required")
    root = Path(coord_root)
    moment = now or datetime.now(timezone.utc)
    report = {"count": 0, "alerts": [], "suppressed": [], "waiting": [], "skipped": []}
    try:
        orders = _orders(root)
    except OSError as exc:
        # Unreadable order directory: still worth alerting, just without names.
        orders = []
        report["orders_error"] = str(exc)
    for row in held_rows if isinstance(held_rows, (list, tuple)) else []:
        try:
            bucket, entry = _one(root, row, orders, moment=moment,
                                 recipient=recipient, sender=sender)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            bucket, entry = "skipped", {"task": _task_of(row),
                                        "reason": f"{type(exc).__name__}: {exc}"}
        report[bucket].append(entry)
    report["count"] = len(report["alerts"])
    return report


def _one(root, row, orders, *, moment, recipient, sender):
    """Classify one held row and, when it deserves it, write its message."""
    if not isinstance(row, dict):
        return "skipped", {"task": "", "reason": "held row is not an object"}
    task = row.get("task")
    if not isinstance(task, str) or not task.strip():
        return "skipped", {"task": "", "reason": "held row has no task"}
    task = task.strip()
    if row.get("over_limit") is not True:
        # Under the limit, or a row that never got one.  The wait is real but it
        # is not yet a decision for a person.
        return "waiting", {"task": task,
                           "waited_seconds": _seconds(row.get("unacked_for_seconds")),
                           "limit_seconds": _seconds(row.get("limit_seconds"))}
    sent_at = _moment(row.get("sent_at"))
    if sent_at is None:
        # Without the send time there is no honest "how long" and no stable id,
        # and an unstable id is one alert per tick.
        return "skipped", {"task": task, "reason": "no parseable sent_at"}
    event_id = str(row.get("event_id") or "").strip()
    waited = _seconds(row.get("unacked_for_seconds"))
    if waited is None:
        waited = max(0, int((moment - sent_at).total_seconds()))
    limit = _seconds(row.get("limit_seconds"))

    order = _find_order(orders, task=task, event_id=event_id)
    agent = str(order.get("target") or "").strip() if order else ""
    order_id = str(order.get("id") or "").strip() if order else ""
    to = _recipient(recipient or (str(order.get("sender") or "").strip() if order else ""))
    if to == sender:
        to = DEFAULT_RECIPIENT if DEFAULT_RECIPIENT != sender else "operator"

    message_id = _message_id(sent_at, f"{task}|{event_id}|{_stamp(sent_at)}")
    path = root / "messages" / to / f"{message_id}.json"
    # The id stays compact because the tooling parses it; a person reads the ISO
    # form, which is the same value the supervisor reported.
    readable = sent_at.isoformat()
    body = _body(task=task, agent=agent or "unknown", order_id=order_id,
                 event_id=event_id, state=str(row.get("state") or ""),
                 sent_at=readable, waited=waited, limit=limit, coord_root=str(root))
    message = {"id": message_id, "at": moment.isoformat(), "sender": sender, "to": to,
               "topic": task, "kind": ALERT_KIND, "text": body,
               # The envelope's own fields stay exactly as ``coord-send`` writes
               # them; the evidence rides along in one extra key, the way
               # ``coord-send --dispatch`` already attaches ``desktop_delivery``.
               "hold": {"task": task, "agent": agent or "unknown", "order": order_id,
                        "event_id": event_id, "state": str(row.get("state") or ""),
                        "sent_at": readable, "unacked_for_seconds": waited,
                        "limit_seconds": limit, "over_limit": True,
                        "order_found": order is not None}}
    if not _write_once(path, message):
        return "suppressed", {"task": task, "message_id": message_id,
                              "reason": "already_alerted"}
    return "alerts", {"task": task, "agent": agent or "unknown", "order": order_id,
                      "event_id": event_id, "message_id": message_id, "to": to,
                      "waited_seconds": waited}


def _body(*, task, agent, order_id, event_id, state, sent_at, waited, limit, coord_root):
    """The part a person reads.  Names the task, the agent, the order and the wait."""
    token = f"AEP-ACK:{event_id}" if event_id else "AEP-ACK"
    waited_text = f"{waited}s" + (f" ({waited // 60} min)" if waited >= 60 else "")
    lines = [
        "AEP 超限告警 / HOLD PAST LIMIT",
        f"task: {task}",
        f"agent: {agent}",
        f"order: {order_id or '(order record not found)'}"
        f"  event: {event_id or '(unknown)'}",
        f"order state: {state or '(unknown)'}",
        f"sent_at: {sent_at}",
        f"waited: {waited_text}  limit: {limit if limit is not None else '(none)'}s",
        "",
        f"这单已经投递，但 {agent} 的会话里始终没有出现 {token}。",
        "任务仍是 running，下游仍是 pending：超限不推进任何东西，这是刻意的。",
        "",
    ]
    if order_id:
        lines += [
            "结束等待需要签字（结果是 blocked，绝不是 done）：",
            f"  aep --coord-root {coord_root} coord-ack --order-id {order_id} "
            '--abandon --by <你> --reason "<理由>"',
        ]
    else:
        lines += [f"订单记录未找到：先查 {coord_root}/commander/inbox 里 task={task} 的单。"]
    return "\n".join(lines) + "\n"


def _orders(root):
    """Every order on disk, newest last.  Read-only: alerts never edit an order."""
    folder = Path(root) / "commander" / "inbox"
    if not folder.is_dir():
        return []
    rows = []
    for path in sorted(folder.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _find_order(orders, *, task, event_id):
    """The order this hold is about, or ``None``.

    The held row carries no agent -- only the task and the event id -- so the
    name in the alert has to come from the record that was actually sent, not
    from a guess.  Matching on ``event_id`` as well as ``task`` matters after a
    handoff: the same task can have orders addressed to two different agents.
    """
    matches = [order for order in orders
               if str(order.get("task", "")) == task
               and (not event_id or str(order.get("event_id", "")) == event_id)]
    if not matches:
        return None
    return max(matches, key=lambda order: str(order.get("created_at", "")))


def _recipient(value):
    """A mailbox name the rest of the tooling accepts, or the fallback."""
    value = str(value or "").strip().lower()
    return value if SAFE_AGENT.fullmatch(value) else DEFAULT_RECIPIENT


def _message_id(sent_at, identity):
    """A message id ``coord-delivery-status`` accepts, derived from the hold.

    The timestamp half is the order's own send time, so the id says which send
    the alert is about; the hash half keeps two holds on the same task apart.
    Deterministic on purpose: the same hold always computes the same path, which
    is what makes the second tick silent.
    """
    return _stamp(sent_at) + "-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]


def _stamp(moment):
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _moment(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _seconds(value):
    """A whole number of seconds, or ``None``.  ``bool`` is not a duration."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, int(value))


def _task_of(row):
    try:
        return str(row.get("task") or "")
    except AttributeError:
        return ""


def _write_once(path, payload):
    """Write the message unless it is already there.  ``True`` when it was new.

    The check-then-replace is not a lock, but two writers that lose the race
    write the *same* path with the same content, so the inbox still ends up with
    one message.  Replacing through a temp file keeps a crash from leaving a
    truncated alert that would suppress every later one.
    """
    path = Path(path)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return True
