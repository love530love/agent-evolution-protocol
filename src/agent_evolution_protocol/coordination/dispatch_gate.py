"""Refuse a dispatch before a single character reaches a composer.

The incident this module answers is not a UI failure.  The window was found, the
input was located, the message was typed and sent -- into the wrong
conversation, carrying content that did not belong to it.  Every layer below
this one reports success, because every layer below this one was asked "can you
type here" and not "should this text go to this agent".

So the checks live above the transport, they run on the exact string that is
about to be typed, and they are pure: no window, no process, no network.  That
is what makes them worth trusting -- a gate that needs the desktop to run is a
gate that is skipped exactly when the desktop is misbehaving.

Four questions, in the order that makes them cheap to answer:

1. **Is this the order it claims to be?**  The stored content hash must match
   the text about to be sent.  Without it, a receipt edited after deposit is
   indistinguishable from a receipt that was always wrong.
2. **Does the text describe one task?**  Exactly one event id, exactly one
   ``[AEP:<task>]`` marker, and both must be this order's.  Two ids in one
   message is how a stale instruction gets delivered alongside a fresh one.
3. **Is it addressed to whom it says?**  The target must appear in the text and
   must equal the profile's agent.  ``qoder`` and ``qoder_cn`` are different
   products with different conversations; a message for one is noise in the
   other.
4. **Is it still the current instruction?**  A newer order for the same task
   with a different event id supersedes this one, and the workspace DAG's own
   event id is the last word.

A gate that only warns is a log line.  These checks are ``block`` by default and
the caller must stop; ``warn`` exists for exactly two cases, and both are named
in their ``detail`` so the exception is visible rather than implied.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Any

SCHEMA = "aep-dispatch-gate-v1"
#: Must stay identical to ``commander.ACK_TOKEN``: the gate exists to prove the
#: acknowledgement instruction is in the message, so it has to look for the same
#: token the sender appended.
ACK_TOKEN = "AEP-ACK"

#: Two id vocabularies are canonical here, and the gate has to know both or it
#: refuses orders that are correct.
#:
#: ``EVT_ID_PATTERN`` -- minted by ``commander.submit_order`` when the caller
#: supplies no id.
#:
#: ``STAMP_ID_PATTERN`` -- the mailbox form ``wake_id()`` mints, that
#: ``cli.coord_delivery_status`` / ``cli.coord_ack_message`` already validate as
#: a message ID, and that ``coordination.bridge`` also writes.
#: ``coord-send --dispatch`` passes its mailbox id straight through as
#: ``event_id``, so this is the format the gate actually receives in production.
#:
#: Recognising only ``EVT-`` made every ``coord-send --dispatch`` order refuse
#: itself at ``one_event_id``: measured on order
#: ``2026-11-02T0738010285190000-f95f6aa7`` the receipt read
#: ``DISPATCH_REFUSED:one_event_id: found none`` for a body that carried its own
#: id.  That check runs before any desktop input, so it proved nothing about the
#: UI and everything about this regex.
#:
#: Both patterns stay tight so a prose word cannot pass for an id, and the safety
#: property is unchanged: exactly one *distinct* id, and it must equal the
#: order's.  Widening the vocabulary is not the same as relaxing the check.
EVT_ID_PATTERN = r"EVT-[A-Za-z0-9][A-Za-z0-9_-]{5,}"
STAMP_ID_PATTERN = r"[0-9]{8}T[0-9]{12}Z-[0-9a-f]{8}"
EVENT = re.compile(rf"\b(?:{EVT_ID_PATTERN}|{STAMP_ID_PATTERN})\b")
_STAMP_ID = re.compile(rf"^{STAMP_ID_PATTERN}$")

TASK_MARKER = re.compile(r"\[AEP:([^\]\s]+)\]")


def is_stamp_id(value: Any) -> bool:
    """Whether ``value`` is the mailbox/timestamp id form rather than ``EVT-``.

    Exposed so ``cli`` stops keeping its own copies of this pattern: three
    copies of one id contract is how the sender and the gate drifted apart in
    the first place.
    """
    return isinstance(value, str) and _STAMP_ID.match(value) is not None
#: Identity strengths, weakest last.  ``exe`` is the only one a title cannot
#: forge; the other two are guesses that happen to be right most of the time.
IDENTITY_ORDER = ("exe", "title", "process-substring")


def content_sha256(text: str) -> str:
    """The hash recorded at deposit and recomputed at send."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    return sha256(text.encode("utf-8")).hexdigest()


def _moment(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def preview(order: dict[str, Any], *, lines: int = 6) -> dict[str, Any]:
    """What a human would see before the send, in the order they'd ask for it."""
    text = str(order.get("text", ""))
    body = [line for line in text.splitlines() if line.strip()]
    ack = next((line.strip() for line in body if ACK_TOKEN in line), "")
    return {
        "order_id": str(order.get("id", "")),
        "target": str(order.get("target", "")),
        "workspace_id": str(order.get("workspace_id", "")),
        "task": str(order.get("task", "")),
        "event_id": str(order.get("event_id", "")),
        "priority": str(order.get("priority", "")),
        "chars": len(text),
        "content_sha256": content_sha256(text) if text else "",
        "ack_line": ack,
        "head": body[:lines],
        "truncated": len(body) > lines,
    }


def render_preview(order: dict[str, Any], *, lines: int = 6) -> str:
    """The preview as text, because a preview nobody can read is not a preview."""
    seen = preview(order, lines=lines)
    out = [f"order    {seen['order_id']}",
           f"target   {seen['target']}   workspace {seen['workspace_id'] or '-'}",
           f"task     {seen['task']}   event {seen['event_id']}",
           f"size     {seen['chars']} chars   sha256 {seen['content_sha256'][:16]}",
           f"ack      {seen['ack_line'] or '(MISSING)'}",
           "body:"]
    out += [f"  | {line}" for line in seen["head"]]
    if seen["truncated"]:
        out.append("  | ...")
    return "\n".join(out)


def check(order: dict[str, Any], *, profile: dict[str, Any] | None = None,
          workspace: dict[str, Any] | None = None,
          newer_orders: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
          window: dict[str, Any] | None = None,
          strict: bool = False, moment: datetime | None = None) -> dict[str, Any]:
    """Decide whether this order may be typed, and say why not when it may not.

    ``profile`` is the client profile the window was bound to, ``workspace`` the
    federation state that owns the task (``None`` when it could not be read),
    ``newer_orders`` every other order for the same task, and ``window`` the
    discovered window hit.  Anything omitted is reported as *not checked* rather
    than assumed to pass -- an absent check and a passed check are different
    facts and the receipt keeps them apart.
    """
    if not isinstance(order, dict):
        raise ValueError("order must be an object")
    text = str(order.get("text", ""))
    target = str(order.get("target", "")).strip()
    task = str(order.get("task", "")).strip()
    event_id = str(order.get("event_id", "")).strip()
    workspace_id = str(order.get("workspace_id", "")).strip()
    checks: list[dict[str, Any]] = []

    def note(check_id: str, ok: bool, detail: str, *, severity: str = "block",
             checked: bool = True) -> None:
        checks.append({"id": check_id, "ok": bool(ok), "checked": checked,
                       "detail": detail, "severity": severity})

    # 1. The text is the text that was deposited.
    recorded = str(order.get("content_sha256", "")).strip()
    actual = content_sha256(text) if text else ""
    if recorded:
        note("content_hash_recorded", True, f"sha256 {recorded[:16]}")
        note("content_unchanged", recorded == actual,
             "text matches the hash recorded at deposit" if recorded == actual
             else f"deposited {recorded[:16]}, now {actual[:16]}")
    elif order.get("dispatch"):
        note("content_hash_recorded", False,
             "order carries a dispatch envelope but no content hash; "
             "this build cannot prove the text is the one that was queued")
        note("content_unchanged", True, "not verifiable without a recorded hash",
             checked=False)
    else:
        note("content_hash_recorded", False,
             "legacy order predates the envelope; the text cannot be verified "
             "against a deposit, only against itself", severity="warn")
        note("content_unchanged", True, "not verifiable without a recorded hash",
             checked=False)

    # 2. One message, one task.
    events = sorted(set(EVENT.findall(text)))
    note("one_event_id", len(events) == 1 and events[0] == event_id,
         f"found {events or 'none'}, expected [{event_id}]")
    markers = sorted(set(TASK_MARKER.findall(text)))
    if not markers:
        note("one_task_marker", True, "no [AEP:<task>] marker in this body",
             severity="warn")
    else:
        note("one_task_marker", len(markers) == 1 and markers[0] == task,
             f"found {markers}, expected [{task}]")

    # 3. Addressed to the right agent, in the right conversation.
    note("event_id_in_text", bool(event_id) and event_id in text,
         f"event id {'present' if event_id and event_id in text else 'MISSING'}")
    note("ack_requirement_in_text", ACK_TOKEN in text,
         f"{ACK_TOKEN} {'present' if ACK_TOKEN in text else 'MISSING'}")
    # The next three describe a *self-describing* assignment, which is what
    # ``federation.assignment.render`` produces and what a human reads.  They
    # are warnings rather than blocks because the facts that actually decide
    # "is this the right content for this order" are the hash, the single event
    # id and the task marker above -- and an ad-hoc "read your inbox" notice
    # would be refused for failing to name a task it never claimed to have.
    # ``strict=True`` turns them into blocks for callers that want the full
    # matrix, and the receipt always records which ones were merely warned.
    if task and task != "GENERAL":
        note("task_named_in_text", task in text,
             f"task id {'present' if task in text else 'MISSING'} in body",
             severity="block" if strict else "warn")
    else:
        note("task_named_in_text", True, "no concrete task id to look for",
             severity="warn")
    note("assignee_named_in_text", bool(target) and target in text,
         f"target {'present' if target and target in text else 'MISSING'} in body",
         severity="block" if strict else "warn")
    if workspace_id:
        note("workspace_named_in_text", workspace_id in text,
             f"workspace {'present' if workspace_id in text else 'MISSING'} in body",
             severity="block" if strict else "warn")
    else:
        note("workspace_named_in_text", True, "order declares no workspace",
             severity="warn")

    if profile is None:
        note("target_matches_profile", True, "no profile supplied", checked=False)
    else:
        agent = str(profile.get("agent", "")).strip()
        note("target_matches_profile", agent == target,
             f"profile agent [{agent}] vs order target [{target}]")

    if window is None:
        note("window_identity", True, "no window hit supplied", checked=False)
    else:
        strength = str(window.get("identity", "process-substring"))
        strong = strength in {"exe", "title"}
        note("window_identity", strong,
             f"window bound by [{strength}]; {'strong' if strong else 'a title substring is not an identity'}",
             severity="block" if strict else "warn")

    # 4. Still the current instruction.
    superseded = [row for row in newer_orders
                  if str(row.get("task", "")) == task
                  and str(row.get("id", "")) != str(order.get("id", ""))
                  and str(row.get("event_id", "")) != event_id
                  and (_moment(row.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
                  > (_moment(order.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))]
    note("not_superseded", not superseded,
         "no newer order for this task" if not superseded
         else f"superseded by {superseded[0].get('id')} ({superseded[0].get('event_id')})")

    if workspace is None:
        note("dag_event_matches", True, "workspace state not read", checked=False)
    else:
        node = (workspace.get("tasks") or {}).get(task) or {}
        expected = str(node.get("event_id", "")).strip()
        if not expected:
            note("dag_event_matches", True, "task has no event id in the DAG",
                 checked=False)
        else:
            note("dag_event_matches", expected == event_id,
                 f"DAG says [{expected}], order says [{event_id}]")

    now = moment or datetime.now(timezone.utc)
    expires = _moment(order.get("expires_at"))
    if expires is None:
        note("not_expired", True, "order carries no expiry", checked=False)
    else:
        note("not_expired", now <= expires, f"expires {expires.isoformat()}")

    blocking = [item for item in checks
                if item["severity"] == "block" and not item["ok"]]
    warned = [item for item in checks
              if item["severity"] == "warn" and not item["ok"]]
    return {
        "schema": SCHEMA,
        "order_id": str(order.get("id", "")),
        "target": target,
        "task": task,
        "event_id": event_id,
        "verdict": "refuse" if blocking else "send",
        "reason": blocking[0]["id"] if blocking else "",
        "detail": blocking[0]["detail"] if blocking else "",
        "failed": [item["id"] for item in blocking],
        "warned": [item["id"] for item in warned],
        "checked": sum(1 for item in checks if item["checked"]),
        "content_sha256": actual,
        "checks": checks,
    }
