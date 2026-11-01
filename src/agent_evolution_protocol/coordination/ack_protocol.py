"""One answer to "did the target agent actually accept this task".

The token we ask the agent to reply with is written into the message we send.
That single fact makes every naive detector wrong: search the conversation for
``AEP-ACK:<event>`` and the first hit is our own request, echoed back.  So an ACK
is not a string occurrence, it is a *role-attributed event in the right
conversation, recorded after we sent*.  Four guards, and dropping any one of them
produces a detector that reports success while nothing has been accepted:

* ``role == "assistant"`` -- otherwise it is our own line, or the user's.
* ``direction == "incoming"`` -- the same line can be captured twice.
* ``session_id`` equals the order's -- an agent in another conversation answering
  the same question is not this task's answer.  An order that recorded *no*
  session id is a separate case (``session_unknown``): no reply can be attributed
  to it at all, and 6 of the 24 orders on disk here are in that state.
* ``recorded_at > created_at`` -- event ids are stable per task, so a re-delivery
  reuses the id and an old reply would otherwise satisfy the new order.

A *late* reply is accepted.  The distinction that matters is "arrived after we
sent", not "arrived inside some window": a timeout is a statement about our
patience, not about the agent, and treating it as refusal is what produces
duplicate sends.  ``state`` therefore has no ``timeout`` value at all.

``CEASED`` is not one fact.  ``commander`` ends an order there for two different
reasons, and only the note tells them apart: ``DELIVERY_UNPROVEN`` means the
adapter failed before anything was typed, while ``SUBMISSION_UNCONFIRMED`` means
a send *may* have happened and must not be retried automatically.  Collapsing
both into ``not_sent`` states something we do not know -- measured on a real
order here (``2026-10-30T0422243473450000-04d70400``), the verdict read
``not_sent`` for an order whose own last attempt said ``possible send``.  Hence
``delivery()`` has three answers.

The fifth state is ``abandoned``, and it exists so that "we stopped waiting" can
never be confused with "it worked" or "it timed out".  A hold with no ceiling
would pin a task forever, but a *timeout* that advances the DAG would delete the
whole guarantee this module provides -- so ending the wait requires a named human
and a written reason, and its consequence is ``blocked``, not ``done``.  A late
reply is still accepted after an abandonment: the agent did answer, we simply
stopped asking, and the record keeps both facts.  Keeping both means the verdict
reports the reply *and* the sign-off while the order's own ``state`` stays
``ABANDONED`` -- a sign-off is not a cache entry that a later event invalidates,
and treating it as one let a late ACK complete a task a human had closed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SCHEMA = "aep-ack-verdict-v1"
#: Must stay identical to ``commander.ACK_TOKEN`` and ``dispatch_gate.ACK_TOKEN``.
ACK_TOKEN = "AEP-ACK"
#: Order states that prove characters reached the conversation.
SENT_STATES = frozenset({"INPUT_SUBMITTED", "MESSAGE_VISIBLE", "AGENT_ACK"})
#: Prefix ``commander`` writes on a ``CEASED`` attempt whose send may have
#: happened.  Matched as a prefix rather than a whole sentence so the note can
#: gain explanation without silently losing its meaning here.
UNCONFIRMED_NOTE = "SUBMISSION_UNCONFIRMED"
#: ``CEASED`` prefixes that say nothing was typed.  Recorded so the two kinds of
#: ``CEASED`` are both named in one place instead of one being implied.
UNPROVEN_NOTE = "DELIVERY_UNPROVEN"
#: The order state a human sets when they stop waiting.  Deliberately *not* a
#: timeout: a timeout is our patience expiring, this is someone signing off, and
#: the two must not share a word or the guarantee becomes a schedule.
ABANDONED_STATE = "ABANDONED"

#: Why a token-bearing event was not accepted.  Each one is a real failure mode,
#: and keeping them apart is the difference between "no answer yet" and "the
#: wrong conversation answered".
REJECTIONS = {
    "not_assistant": "the line is not attributed to the agent; our own request reads the same",
    "not_incoming": "the line was captured as outgoing, so it is something we sent",
    "foreign_session": "the reply came from a different conversation than the order's",
    "session_unknown": ("the order recorded no session id, so no reply can be attributed to "
                        "it -- not the same fact as a reply from elsewhere, and not fixable by "
                        "looking harder in this conversation"),
    "stale": "the reply predates this order; event ids are stable per task, so this is an older delivery's answer",
    "no_timestamp": "the reply carries no timestamp, so it cannot be placed after the send",
}


def token_for(event_id: str) -> str:
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id is required")
    return f"{ACK_TOKEN}:{event_id}"


def _moment(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def delivery(order: dict[str, Any]) -> str:
    """``proven`` / ``unconfirmed`` / ``none`` -- what the ladder actually shows.

    ``proven`` means an attempt recorded a state that cannot exist without the
    characters having reached the conversation.  ``unconfirmed`` means an attempt
    ended in ``CEASED`` with a note saying a send may have happened; that is not
    evidence of delivery, but it is also not evidence of the opposite, and the
    two must not share a word.

    ``state_before_abandon`` is read as well, because abandoning an order
    overwrites ``state`` with ``ABANDONED`` and would otherwise delete the only
    place the delivery was recorded -- an action that erases a fact is worse than
    no action.  Measured on a hand-built order that reached ``MESSAGE_VISIBLE``
    without an attempt row: after the sign-off the verdict read ``delivery:
    none``, ``sent: false``, i.e. the abandonment had un-sent the message.
    """
    if not isinstance(order, dict):
        raise ValueError("order must be an object")
    attempts = [attempt for attempt in order.get("attempts") or [] if isinstance(attempt, dict)]
    for candidate in (order.get("state", ""), order.get("state_before_abandon", "")):
        if str(candidate) in SENT_STATES:
            return "proven"
    if any(str(attempt.get("state", "")) in SENT_STATES for attempt in attempts):
        return "proven"
    if any(str(attempt.get("note", "")).startswith(UNCONFIRMED_NOTE) for attempt in attempts):
        return "unconfirmed"
    return "none"


def sent(order: dict[str, Any]) -> bool:
    """Whether anything was *proven* to reach this order's conversation.

    Deliberately narrow: the supervisor's hold uses this, and an order whose send
    is merely possible must not pin a task waiting for a reply that may have
    never been asked for.
    """
    return delivery(order) == "proven"


def abandoned(order: dict[str, Any]) -> dict[str, Any] | None:
    """The human sign-off that ended the wait, or ``None``.

    Returns the recorded facts rather than a boolean, for the same reason the
    verdict returns ``rejected`` instead of a bare ``False``: "abandoned" with no
    "by whom, when, why" is not something an operator can act on, and it is also
    not something an audit can rely on.
    """
    if not isinstance(order, dict):
        raise ValueError("order must be an object")
    if str(order.get("state", "")) != ABANDONED_STATE:
        return None
    return {
        "by": str(order.get("abandoned_by", "")),
        "at": str(order.get("abandoned_at", "")),
        "reason": str(order.get("abandon_reason", "")),
    }




def classify(event: dict[str, Any], *, token: str, session_id: str,
             created_at: Any) -> dict[str, Any]:
    """Judge one candidate event.  Returns ``accepted`` plus the reason it was not."""
    row = {
        "event": str(event.get("id", "")),
        "role": str(event.get("role", "")),
        "direction": str(event.get("direction", "")),
        "session_id": str(event.get("session_id", "")),
        "recorded_at": str(event.get("recorded_at", "")),
        "accepted": False,
        "reason": "",
    }
    if event.get("role") != "assistant":
        row["reason"] = "not_assistant"
        return row
    if event.get("direction") != "incoming":
        row["reason"] = "not_incoming"
        return row
    if not session_id:
        # Measured on the real inbox: 6 of 24 orders carry no session id, so this
        # branch decides their verdict forever.  Calling it ``foreign_session``
        # would claim the reply came from somewhere else, which we do not know --
        # and it would send an operator hunting through other windows for an
        # answer that could never be accepted.  The true statement is narrower:
        # nothing can be attributed to a conversation that was never recorded.
        row["reason"] = "session_unknown"
        return row
    if row["session_id"] != session_id:
        row["reason"] = "foreign_session"
        return row
    moment = _moment(row["recorded_at"])
    deposited = _moment(created_at)
    if moment is None:
        row["reason"] = "no_timestamp"
        return row
    if deposited is not None and moment <= deposited:
        row["reason"] = "stale"
        return row
    row["accepted"] = True
    return row


def verdict(order: dict[str, Any], *, events: list[dict[str, Any]],
            now: datetime | None = None) -> dict[str, Any]:
    """The single ACK judgement for one order.

    ``events`` is the transcript for this order's target, as recorded.  Nothing
    else is consulted: a screen snapshot, a composer draft or a process that is
    still running cannot produce an ACK, and pretending otherwise is how "the
    window is focused" gets promoted to "the agent is working".
    """
    if not isinstance(order, dict):
        raise ValueError("order must be an object")
    event_id = str(order.get("event_id", "")).strip()
    if not event_id:
        raise ValueError("order has no event_id")
    token = token_for(event_id)
    session_id = str(order.get("session_id", ""))
    created_at = order.get("created_at", "")

    candidates = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        lines = "\n".join(str(line) for line in (event.get("added_lines") or []))
        if token not in lines:
            continue
        candidates.append(classify(event, token=token, session_id=session_id,
                                   created_at=created_at))
    accepted = [row for row in candidates if row["accepted"]]
    reached = delivery(order)
    signoff = abandoned(order)
    if accepted:
        state = "acked"
    elif signoff is not None:
        # Still not an ACK, but no longer "waiting".  Reporting sent_unacked here
        # would leave a supervisor waiting for an answer to a question nobody is
        # asking any more -- and "abandoned" is the only state that lets the
        # consequence be "blocked for a human" instead of "done".
        state = "abandoned"
    elif reached == "proven":
        state = "sent_unacked"
    elif reached == "unconfirmed":
        # Not "waiting" and not "never sent": a fourth answer, because the two
        # existing ones would both be false claims about what happened.
        state = "submitted_unconfirmed"
    else:
        state = "not_sent"

    rejected = [row for row in candidates if not row["accepted"]]
    return {
        "schema": SCHEMA,
        "order_id": str(order.get("id", "")),
        "target": str(order.get("target", "")),
        "task": str(order.get("task", "")),
        "event_id": event_id,
        "token": token,
        "state": state,
        "accepted": bool(accepted),
        "sent": reached == "proven",
        "delivery": reached,
        "sent_at": str(created_at),
        "abandoned": signoff,
        # Two facts, neither hidden: a reply exists *and* a person ended the
        # wait.  ``late_ack`` names the combination so no reader has to derive
        # it from two fields and get it wrong; ``late_acks`` is what
        # ``commander`` recorded durably when it declined to move the order.
        "late_ack": bool(accepted) and signoff is not None,
        "late_acks": [dict(row) for row in order.get("late_acks") or []
                      if isinstance(row, dict)],
        "evidence": accepted[0] if accepted else None,
        "candidates": len(candidates),
        "rejected": rejected,
        "reason": "" if accepted else (rejected[0]["reason"] if rejected else ""),
        "at": (now or datetime.now(timezone.utc)).isoformat(),
    }


def may_advance(value: dict[str, Any]) -> bool:
    """The rule the supervisor uses: no accepted ACK, no downstream progress.

    A sign-off outranks an ACK here even though both are on record.  The
    supervisor checks ``abandoned`` before this function, so in practice the
    guard is belt-and-braces; it is still written down because ``may_advance``
    is the documented answer to "may the DAG move", and "a machine event may
    revoke a human decision" is the one answer that must never come out of it.
    """
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        return False
    if value.get("abandoned") is not None:
        return False
    return value.get("accepted") is True
