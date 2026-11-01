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
  the same question is not this task's answer.
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
``delivery()`` has three answers and ``state`` has four.
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

#: Why a token-bearing event was not accepted.  Each one is a real failure mode,
#: and keeping them apart is the difference between "no answer yet" and "the
#: wrong conversation answered".
REJECTIONS = {
    "not_assistant": "the line is not attributed to the agent; our own request reads the same",
    "not_incoming": "the line was captured as outgoing, so it is something we sent",
    "foreign_session": "the reply came from a different conversation than the order's",
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
    """
    if not isinstance(order, dict):
        raise ValueError("order must be an object")
    attempts = [attempt for attempt in order.get("attempts") or [] if isinstance(attempt, dict)]
    if str(order.get("state", "")) in SENT_STATES:
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
    if not session_id or row["session_id"] != session_id:
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
    if accepted:
        state = "acked"
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
        "evidence": accepted[0] if accepted else None,
        "candidates": len(candidates),
        "rejected": rejected,
        "reason": "" if accepted else (rejected[0]["reason"] if rejected else ""),
        "at": (now or datetime.now(timezone.utc)).isoformat(),
    }


def may_advance(value: dict[str, Any]) -> bool:
    """The rule the supervisor uses: no accepted ACK, no downstream progress."""
    return isinstance(value, dict) and value.get("schema") == SCHEMA and value.get("accepted") is True
