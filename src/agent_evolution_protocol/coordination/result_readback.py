"""Did the agent produce the thing it said it produced.

Two facts are already provable here and neither is this one.  ``ack_protocol``
proves an order reached the right conversation and that the agent answered in
it.  ``provider_readback`` proves how far a transport got.  What none of them
proves is the work itself: ``assignment.complete_evidence()`` still accepts the
agent writing ``phase=done`` about itself, so "the task is finished" is
currently the agent's own word with a receipt stapled to it.

This module compares the two statements that exist -- what the agent *claimed*
in its ``coord-task-state`` write-back, and what the transcript shows it *said*
-- and it refuses to answer when only one of them is present.  Three properties
carry the whole thing:

* **``match`` is three-valued.**  ``None`` means "the transcript has nothing to
  compare", and it is not ``True``.  An empty transcript is the normal state of
  a capture that failed, and reading it as agreement is how a readback becomes
  a decoration.  ``may_complete()`` therefore accepts only ``True``.
* **An artifact is a path, not a word.**  Evidence that says ``done`` or
  ``tests pass`` names nothing a second party can look at, so it is kept in
  ``unchecked`` and the verdict is ``None`` -- not a pass, and not a failure
  either.
* **The transcript has to be attributable.**  A path in our own outgoing
  assignment is not the agent naming its output, and a path in another
  conversation is not this task's result.  Same guards as the ACK detector,
  for the same reason.

Why there is no new schema here.  ``aep-readback-v1`` is a *receipt* about a
transport ladder: a closed set of five states, two booleans, and a list of
strings.  It has no field that can hold "compared and disagreed", none that can
hold "there was nothing to compare", and its ``accepted()`` is defined on
``AGENT_ACK`` -- the acceptance claim, which is exactly the claim this module
exists to test.  Writing a verdict into that shape would mean either inventing a
sixth state its own acceptance test rejects, or encoding ``None`` as
``observed: False``, which states the opposite of what happened.  So this module
emits a judgement with no ``schema`` key at all, the way
``session_fingerprint.compare()`` does: it consumes a readback, it is not one.
A second readback contract is the mistake this repository has already paid for
once; a judgement that borrows someone else's name would be the same mistake
with better manners.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

#: The contract this module *reads*.  It writes none of its own; see the module
#: docstring for why bending a receipt into a verdict was refused.
READBACK_SCHEMA = "aep-readback-v1"
#: Mirrors ``assignment.DONE_PHASES``.  Kept as a literal so this module stays
#: import-free of the federation layer; the test suite asserts they agree.
DONE_PHASES = frozenset({"done", "completed", "complete"})

#: Why a verdict is what it is.  Every one of these is a different situation and
#: a supervisor acts differently on each, so they are never collapsed into a
#: bare boolean.
REASONS = {
    "claim_not_done": "the agent did not write a done phase; there is nothing to verify yet",
    "claim_without_artifact": "the agent claimed completion but named no path anyone could check",
    "artifact_missing_on_disk": "a claimed artifact does not exist under the given root",
    "foreign_session": "the only result-bearing lines came from a different conversation",
    "different_artifact": "the transcript names an artifact that is not the one claimed",
    "no_result_in_transcript": "the transcript carries nothing comparable to the claim",
}

#: A path-shaped token inside a conversation line.  Requires at least one
#: separator and a file extension, so prose ("and/or", "24/7") does not become
#: an artifact and a directory name alone does not either.
_ARTIFACT = re.compile(r"(?:[A-Za-z]:[\\/]|/)?(?:[\w.@+\-]+[\\/])+[\w.@+\-]+\.[A-Za-z0-9]{1,8}")
#: Characters that wrap a path in a sentence or a markdown code span.  A colon is
#: deliberately absent: it is part of a drive letter.
_EDGE = " \t\r\n`'\"“”‘’<>()[]{}，。；、,;"


def _normalise_path(raw: Any) -> str:
    """One spelling per file, decided here and not by the host.

    The same lesson as ``session_fingerprint._normalise``: ``os.path.normcase``
    is the identity function on POSIX, so a path recorded on Windows would
    never compare equal under WSL and a digest that only works on one host is
    not a digest.  The spelling is preserved rather than folded -- the verdict
    is read by a human and then used to open a file -- and case is ignored only
    where the two sides are both drive-letter paths, which Windows itself does
    not distinguish.
    """
    text = str(raw or "").strip(_EDGE)
    if not text:
        return ""
    text = text.replace("\\", "/")
    text = re.sub(r"(?<!^)/{2,}", "/", text)
    return text.rstrip("/")


def _is_drive(path: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:/", path))


def _segments(path: str) -> list[str]:
    return [part for part in path.split("/") if part]


def _looks_like_path(raw: Any) -> bool:
    """Whether this evidence entry names something a second party can open."""
    text = _normalise_path(raw)
    if not text or " " in text:
        return False
    if re.match(r"^[A-Za-z]:/", text):
        return True
    parts = _segments(text)
    if len(parts) < 2:
        return False
    return "." in parts[-1]


def _same_artifact(left: str, right: str) -> bool:
    """Equal, or one is a suffix of the other with at least two real segments.

    The two-segment floor is what stops ``report.md`` in the transcript from
    confirming ``K:/proj/old/report.md`` in the claim: a bare filename is a
    different file until proven otherwise, and the direction of that doubt
    matters -- refusing costs one re-read, accepting costs a wrong completion.
    """
    if not left or not right:
        return False
    if left == right:
        return True
    a, b = left, right
    if _is_drive(a) and _is_drive(b):
        a, b = a.casefold(), b.casefold()
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(_segments(short)) < 2:
        return False
    return long.endswith("/" + short)


def claim(task_state: dict[str, Any]) -> dict[str, Any]:
    """What the agent declared about itself, split into checkable and not.

    Accepts either the raw ``task_states/<task>.json`` payload or the report
    ``assignment.complete_evidence()`` returns; the fields they share mean the
    same thing, and a supervisor that has one should not have to convert it.
    """
    if not isinstance(task_state, dict):
        raise ValueError("task_state must be an object")
    phase = str(task_state.get("phase", "")).strip().lower()
    if "done" in task_state:
        done = task_state.get("done") is True
    else:
        done = phase in DONE_PHASES
    raw = [str(item).strip() for item in (task_state.get("evidence") or []) if str(item).strip()]
    artifacts: list[str] = []
    unchecked: list[str] = []
    for item in raw:
        normalised = _normalise_path(item)
        if _looks_like_path(item):
            if normalised not in artifacts:
                artifacts.append(normalised)
        else:
            unchecked.append(item)
    return {
        "task": str(task_state.get("task", "")),
        "agent": str(task_state.get("owner") or task_state.get("agent") or ""),
        "phase": phase,
        "done": done,
        "summary": str(task_state.get("goal") or task_state.get("next_action") or ""),
        "artifacts": artifacts,
        "unchecked": unchecked,
    }


def observe(events: list[dict[str, Any]], *, session_id: str = "") -> dict[str, Any]:
    """What the transcript shows the agent said, line group by line group.

    ``role == "assistant"`` is required, ``direction == "outgoing"`` is refused,
    and a session that disagrees with the claim's is refused.  An event that
    carries *no* session id is not refused: a capture gap is not evidence of a
    foreign conversation, and ``session_checked`` records whether any event was
    actually compared, so a reader can see that the guard did not fire rather
    than assume it passed.
    """
    rows: list[dict[str, Any]] = []
    artifacts: list[str] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        lines = event.get("added_lines") or []
        if isinstance(lines, str):
            lines = [lines]
        row = {
            "event": str(event.get("id", "")),
            "role": str(event.get("role", "")),
            "direction": str(event.get("direction", "")),
            "session_id": str(event.get("session_id", "")),
            "recorded_at": str(event.get("recorded_at", "")),
            "attributable": False,
            "reason": "",
            "artifacts": [],
        }
        if row["role"] != "assistant":
            row["reason"] = "not_assistant"
        elif row["direction"] == "outgoing":
            row["reason"] = "not_incoming"
        elif session_id and row["session_id"] and row["session_id"] != session_id:
            row["reason"] = "foreign_session"
        else:
            row["attributable"] = True
            found_here: list[str] = []
            for line in lines:
                for found in _ARTIFACT.findall(str(line)):
                    candidate = _normalise_path(found)
                    if candidate and candidate not in found_here:
                        found_here.append(candidate)
            row["artifacts"] = found_here
            for candidate in found_here:
                if candidate not in artifacts:
                    artifacts.append(candidate)
        rows.append(row)
    return {
        "events": rows,
        "attributable": [row["event"] for row in rows if row["attributable"]],
        "rejected": [{"event": row["event"], "reason": row["reason"]}
                     for row in rows if not row["attributable"]],
        "artifacts": artifacts,
        "session_checked": bool(session_id) and any(row["session_id"] for row in rows),
    }


def judge(claim_value: dict[str, Any], observed: dict[str, Any], *,
          root: Any = None, at: datetime | None = None) -> dict[str, Any]:
    """Compare one claim against one observation.  ``match`` is ``True``/``False``/``None``.

    ``root`` is optional and only used for the one check that needs a disk: if
    the agent named a path that is not there, the claim is false whatever the
    transcript says, because a missing file is a fact and a missing mention is
    an interpretation.  Without a root the module does not guess.
    """
    if not isinstance(claim_value, dict) or not isinstance(observed, dict):
        raise ValueError("claim and observation must be objects")

    claimed = list(claim_value.get("artifacts") or [])
    observed_artifacts = list(observed.get("artifacts") or [])
    rows = list(observed.get("events") or [])
    missing_on_disk: list[str] = []

    def verdict(match: bool | None, reason: str, seen: list[str] | None = None) -> dict[str, Any]:
        return {
            "match": match,
            "reason": reason,
            "task": str(claim_value.get("task", "")),
            "agent": str(claim_value.get("agent", "")),
            "phase": str(claim_value.get("phase", "")),
            "claim_done": bool(claim_value.get("done")),
            "claimed": claimed,
            "seen": list(seen or []),
            "missing_on_disk": missing_on_disk,
            "evidence": {
                "observed_artifacts": observed_artifacts,
                "events": rows,
                "rejected": list(observed.get("rejected") or []),
                "unchecked": list(claim_value.get("unchecked") or []),
                "session_checked": bool(observed.get("session_checked")),
                "root": str(root or ""),
            },
            "at": (at or datetime.now(timezone.utc)).isoformat(),
        }

    if not claim_value.get("done"):
        # A ``working`` write-back with paths in it is progress, not completion.
        # Verifying the paths would let the next heartbeat close the task.
        return verdict(None, "claim_not_done")
    if not claimed:
        return verdict(None, "claim_without_artifact")

    if root:
        base = Path(str(root))
        for item in claimed:
            candidate = Path(item)
            if not candidate.is_absolute():
                candidate = base / item
            if not candidate.exists():
                missing_on_disk.append(item)
        if missing_on_disk:
            return verdict(False, "artifact_missing_on_disk")

    if not rows:
        return verdict(None, "no_result_in_transcript")
    if not observed.get("attributable"):
        if any(row.get("reason") == "foreign_session" for row in rows):
            return verdict(False, "foreign_session")
        return verdict(None, "no_result_in_transcript")
    if not observed_artifacts:
        return verdict(None, "no_result_in_transcript")

    seen = [item for item in claimed
            if any(_same_artifact(item, other) for other in observed_artifacts)]
    unmatched = [item for item in claimed if item not in seen]
    if not unmatched:
        return verdict(True, "", seen=seen)
    # Something was produced, and it is not what was claimed.  Partial evidence
    # is a third thing: what was named checks out, what was not named stays
    # unknown, and only a *different* file in the transcript is a disagreement.
    if any(not any(_same_artifact(item, other) for item in claimed)
           for other in observed_artifacts):
        return verdict(False, "different_artifact", seen=seen)
    return verdict(None, "no_result_in_transcript", seen=seen)


def may_complete(value: dict[str, Any]) -> bool:
    """The one rule the supervisor gates on: verified, done, and no caveat.

    Anything other than ``match is True`` -- including ``None`` -- refuses.  The
    ``reason`` check is not redundant: a dict that claims a match while carrying
    a disagreement is exactly the shape a bug produces, and it must not pass.
    """
    return (isinstance(value, dict)
            and value.get("match") is True
            and value.get("claim_done") is True
            and not value.get("reason"))
