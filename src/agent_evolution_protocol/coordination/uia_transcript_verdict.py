"""An INDEPENDENT post-send verdict read from a UIA transcript capture.

Measured 2026-11-06 on ChatGPT (event 20261106T065238959490Z-9fd49a50):
a compose receipt reported ``task_delivered=false`` with all four OCR band
checks failing, while a read-only ``coord-uia capture`` showed the event id
inside the conversation and the agent already running a read-only audit. The
OCR band split is computed from window-relative ratios and is wrong on a
multi-monitor window whose rectangle starts at (960, 429); that is a false
negative, not a failed delivery.

This module does NOT repair the OCR formula and does NOT replace
``action_verification``. It adds a second, recomputable source that a caller
can put BESIDE the existing four checks. Disagreement is reported as a fact;
it never upgrades the primary verdict. A UIA tree is only available for
clients that build one (for example ChatGPT and Qoder), so this source is
unavailable -- ``verified=None`` -- for OCR-only clients rather than guessed.

Boundary rule: the last line containing the profile's composer placeholder is
the first line of the composer band. Everything above it is conversation.
Without an exact hint the boundary is unknown, never inferred from a guessed
line count, because a draft that merely sits in the composer contains the same
event id as a delivered message.
"""
from __future__ import annotations

from typing import Any

from . import action_verification as av

SCHEMA = "aep-uia-transcript-verdict-v1"

NO_TRANSCRIPT = "NO_TRANSCRIPT"
NO_EVENT_ID = "NO_EVENT_ID"
NO_COMPOSER_BOUNDARY = "NO_COMPOSER_BOUNDARY"
STILL_IN_COMPOSER = "STILL_IN_COMPOSER"
EVENT_ID_NOT_IN_CONVERSATION = "EVENT_ID_NOT_IN_CONVERSATION"
PROBE_NOT_IN_CONVERSATION = "PROBE_NOT_IN_CONVERSATION"

NOTE = ("Independent second opinion from a read-only UIA transcript capture. "
        "It proves the message reached the visible conversation; it is not an "
        "ACK, not a reply and not a VERIFIED handoff.")


def _boundary(lines: list[str], hint: str) -> dict[str, Any]:
    """Index of the LAST line holding the composer placeholder."""
    index = -1
    for position, line in enumerate(lines):
        if hint and hint in line:
            index = position
    if index < 0:
        return {"method": "absent", "line_index": -1, "hint": hint}
    return {"method": "last-placeholder-line", "line_index": index, "hint": hint}


def _unknown(reason: str, *, event_id: str, transcript: str,
             boundary: dict[str, Any]) -> dict[str, Any]:
    return {"schema": SCHEMA, "source": "uia-transcript", "verified": None,
            "reason": reason, "event_id": event_id,
            "transcript_chars": len(transcript), "boundary": boundary,
            "checks": [], "evidence": {}, "note": NOTE}


def evaluate(*, transcript: str, event_id: str, probe: str = "",
             composer_hint: str = "") -> dict[str, Any]:
    """Did the event id move into the conversation and leave the composer?

    ``verified`` is ``True`` only when the id is above the boundary, absent
    below it, and (when supplied) the probe fragment is above it too. Missing
    inputs produce ``None``, never a pass.
    """
    text = transcript or ""
    event_id = (event_id or "").strip()
    probe = (probe or "").strip()
    hint = (composer_hint or "").strip()
    lines = text.splitlines()
    absent_boundary: dict[str, Any] = {"method": "absent", "line_index": -1,
                                       "hint": hint}

    if not text.strip():
        return _unknown(NO_TRANSCRIPT, event_id=event_id, transcript=text,
                        boundary=absent_boundary)
    if not event_id:
        return _unknown(NO_EVENT_ID, event_id=event_id, transcript=text,
                        boundary=absent_boundary)
    if not hint:
        return _unknown(NO_COMPOSER_BOUNDARY, event_id=event_id, transcript=text,
                        boundary=absent_boundary)
    boundary = _boundary(lines, hint)
    if boundary["line_index"] < 0:
        return _unknown(NO_COMPOSER_BOUNDARY, event_id=event_id, transcript=text,
                        boundary=boundary)

    cut = int(boundary["line_index"])
    conversation = "\n".join(lines[:cut])
    composer = "\n".join(lines[cut:])
    in_conversation = event_id in conversation
    in_composer = event_id in composer
    probe_fragment = av.fragment_match(probe, conversation) if probe else ""
    probe_ok = not probe or bool(probe_fragment)

    checks = [
        av._check("event_id_in_conversation",
                  av.PASS if in_conversation else av.FAIL,
                  evidence={"chars": len(conversation)},
                  reason="" if in_conversation else EVENT_ID_NOT_IN_CONVERSATION),
        av._check("event_id_not_in_composer",
                  av.PASS if not in_composer else av.FAIL,
                  evidence={"chars": len(composer)},
                  reason="" if not in_composer else STILL_IN_COMPOSER),
    ]
    if probe:
        checks.append(av._check(
            "probe_in_conversation", av.PASS if probe_ok else av.FAIL,
            evidence={"probe": probe, "fragment": probe_fragment},
            reason="" if probe_ok else PROBE_NOT_IN_CONVERSATION,
            note="tolerant matching gives up characters; it never loosens them"))
    else:
        checks.append(av._check(
            "probe_in_conversation", av.UNKNOWN, evidence={"probe": ""},
            reason="NO_PROBE",
            note="no probe was supplied; the event id alone carries this verdict"))

    if in_composer:
        reason = STILL_IN_COMPOSER
    elif not in_conversation:
        reason = EVENT_ID_NOT_IN_CONVERSATION
    elif not probe_ok:
        reason = PROBE_NOT_IN_CONVERSATION
    else:
        reason = ""

    return {"schema": SCHEMA, "source": "uia-transcript",
            "verified": not reason, "reason": reason, "event_id": event_id,
            "transcript_chars": len(text), "boundary": boundary,
            "checks": checks,
            "evidence": {"in_conversation": in_conversation,
                         "in_composer": in_composer,
                         "probe_fragment": probe_fragment,
                         "conversation_chars": len(conversation),
                         "composer_chars": len(composer)},
            "note": NOTE}
