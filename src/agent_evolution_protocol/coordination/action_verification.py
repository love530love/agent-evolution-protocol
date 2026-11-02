"""What would prove the action happened, stated before it is taken.

A computer-use loop is look, act, look again, assert.  AEP already had the first
two and the third only as scattered booleans inside a PowerShell adapter, which
meant ``input_routing.delivery_verdict`` could say ``send-after-verification``
without anything defining what verification was.  This module is that
definition, and it is deliberately small: three checks that are actually
performed today, an aggregate that refuses to guess, and a matcher that encodes
what the OCR path learned the hard way.

Two rules are load-bearing:

1. **A missing check is ``None``, never ``False`` and never ``True``.**  "No
   verifier was installed" and "the verifier said no" are different facts with
   different remedies, and collapsing either into a boolean is how a run
   proceeds on an assumption.
2. **Matching tolerates character substitution, but only by giving up
   characters.**  The Chinese OCR recogniser on this host turns ``键`` into
   ``鍵`` and ``0`` into ``O``, so exact matching missed a paste that had
   actually landed (measured 2026-11-02).  Shrinking the needle from the front
   keeps the evidence; loosening the comparison would also accept text that was
   never typed.
"""
from __future__ import annotations

from typing import Any, Iterable

SCHEMA = "aep-action-verification-v1"

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"

#: Below this a fragment matches by accident -- four characters of Chinese is
#: already short, and the OCR path settled on the same floor independently.
MIN_FRAGMENT_CHARS = 4


def fragment_match(needle: str, haystack: str,
                   *, min_chars: int = MIN_FRAGMENT_CHARS) -> str:
    """The longest surviving fragment of ``needle`` inside ``haystack``.

    Tries the whole needle first, then gives up one trailing character at a
    time.  Giving up characters is honest: the returned fragment is the part
    that was really read, and an empty result means nothing recognisable was.
    """
    needle = (needle or "").strip()
    haystack = haystack or ""
    if not needle or not haystack:
        return ""
    for end in range(len(needle), min_chars - 1, -1):
        candidate = needle[:end]
        if candidate in haystack:
            return candidate
    return ""


def _contains(needle: str, haystack: str) -> tuple[bool, str]:
    """Exact match first, then the tolerant one.  The fragment is the evidence."""
    needle = (needle or "").strip()
    if not needle:
        return False, ""
    if needle in (haystack or ""):
        return True, needle
    fragment = fragment_match(needle, haystack)
    return bool(fragment), fragment


def _check(name: str, outcome: str, *, evidence: dict[str, Any], reason: str = "",
           note: str = "") -> dict[str, Any]:
    return {"check": name, "outcome": outcome, "evidence": evidence,
            "reason": reason, "note": note}


def text_present(text: str, scan: str) -> dict[str, Any]:
    """Was this text on screen?"""
    found, fragment = _contains(text, scan)
    return _check("text_present", PASS if found else FAIL,
                  evidence={"fragment": fragment, "needle_chars": len((text or "").strip())},
                  reason="" if found else "NOT_ON_SCREEN",
                  note="" if fragment == (text or "").strip() else
                       "matched on a shortened fragment; the recogniser substituted characters")


def text_absent(text: str, scan: str) -> dict[str, Any]:
    """Was this text gone?  The inverse of :func:`text_present`, stated so a
    caller does not have to remember to negate a boolean."""
    found, fragment = _contains(text, scan)
    return _check("text_absent", FAIL if found else PASS,
                  evidence={"fragment": fragment},
                  reason="STILL_ON_SCREEN" if found else "")


def placeholder_gone(hint: str, scan: str) -> dict[str, Any]:
    """Did the composer go back to showing its placeholder?

    A hint that was never on screen cannot have disappeared, so an empty hint is
    ``unknown`` rather than a pass: reporting success because nothing was
    checked is the exact failure this module exists to prevent.
    """
    hint = (hint or "").strip()
    if not hint:
        return _check("placeholder_gone", UNKNOWN, evidence={"hint": ""},
                      reason="NO_HINT",
                      note="the profile declares no composer hint, so emptiness is unobservable")
    found, fragment = _contains(hint, scan)
    return _check("placeholder_gone", PASS if found else FAIL,
                  evidence={"hint": hint, "fragment": fragment},
                  reason="" if found else "HINT_NOT_VISIBLE",
                  note="an empty composer is the only proof the text left the box")


def _lines_in_band(lines: Iterable[dict[str, Any]], *, top: int, bottom: int) -> list[str]:
    band: list[str] = []
    for line in lines:
        try:
            y = int(line.get("y", -1))
        except (TypeError, ValueError):
            continue
        if top <= y < bottom:
            band.append(str(line.get("text", "")))
    return band


def text_moved(probe: str, *, lines: Iterable[dict[str, Any]], split_y: int) -> dict[str, Any]:
    """Did the text leave the composer band and turn up above it?

    Presence is not delivery.  A draft that is still in the box, and a message
    that has scrolled into the transcript, both contain the same string; only
    the position separates them, which is why the OCR path measures a band
    boundary instead of asking "is the probe on screen".
    """
    lines = list(lines)
    if not (probe or "").strip():
        return _check("text_moved", UNKNOWN, evidence={"probe": ""}, reason="NO_PROBE")
    if split_y <= 0:
        return _check("text_moved", UNKNOWN, evidence={"split_y": split_y},
                      reason="NO_BAND_BOUNDARY")
    composer = "\n".join(_lines_in_band(lines, top=split_y, bottom=1 << 30))
    transcript = "\n".join(_lines_in_band(lines, top=-1 << 30, bottom=split_y))
    left, left_fragment = _contains(probe, composer)
    arrived, arrived_fragment = _contains(probe, transcript)
    verified = arrived and not left
    if verified:
        reason = ""
    elif left:
        # Still in the box, whether or not a copy also reached the transcript:
        # a stale draft plus a delivered copy is the case that fools a presence
        # check, so it is reported as the same failure.
        reason = "STILL_IN_COMPOSER"
    else:
        reason = "NEVER_ARRIVED"
    return _check("text_moved", PASS if verified else FAIL,
                  evidence={"split_y": split_y, "in_transcript": arrived,
                            "in_composer": left, "fragment": arrived_fragment or left_fragment},
                  reason=reason,
                  note="the probe moved from the composer band into the transcript band")


#: A driver receipt field, and what a true value proves.
SEND_REPORT_CHECKS = {
    "probe_in_composer_before_send": ("text_visible_before_send", True),
    "probe_in_transcript": ("text_arrived", True),
    "probe_in_composer_after": ("text_left_composer", False),
    "placeholder_gone": ("composer_emptied", True),
}


def from_send_report(report: dict[str, Any]) -> dict[str, Any]:
    """Express a driver's own receipt as checks, without inventing any.

    Fields the report does not carry stay absent, so an older adapter produces
    ``unknown`` instead of a confident pass.  ``text_left_composer`` inverts
    because the receipt records presence, not absence -- flipping it here keeps
    the inversion in one place instead of in every caller.
    """
    checks: list[dict[str, Any]] = []
    for field, (name, expected) in SEND_REPORT_CHECKS.items():
        if field not in report:
            continue
        value = report[field]
        if not isinstance(value, bool):
            continue
        outcome = PASS if value is expected else FAIL
        checks.append(_check(name, outcome, evidence={"field": field, "value": value},
                             reason="" if outcome == PASS else f"{field.upper()}_DISAGREES"))
    return {"checks": checks, "source": "send_report"}


def verdict(checks: Iterable[dict[str, Any]], *,
            required: Iterable[str] = ()) -> dict[str, Any]:
    """One answer from many checks, and it refuses to round up.

    ``verified`` is ``True`` only when every required check passed and nothing
    failed.  A failure outranks a missing check because a refutation is
    information and an absence is not.
    """
    checks = [check for check in checks if isinstance(check, dict)]
    by_name = {str(check.get("check", "")): check for check in checks}
    required = [name for name in required]
    missing = [name for name in required if name not in by_name]
    failed = [check for check in checks if check.get("outcome") == FAIL]
    passed = [check for check in checks if check.get("outcome") == PASS]

    if failed:
        first = failed[0]
        verified: bool | None = False
        reason = str(first.get("reason") or "CHECK_FAILED")
    elif missing:
        verified = None
        reason = "MISSING_CHECK"
    elif not checks:
        verified = None
        reason = "NO_CHECKS"
    elif required:
        verified = True
        reason = ""
    else:
        # Nothing was declared required, so nothing can be concluded from a
        # pile of passes -- the caller has not said what would count.
        verified = None
        reason = "NO_REQUIRED_CHECKS"

    return {"schema": SCHEMA, "verified": verified, "reason": reason,
            "checks": checks, "missing": missing,
            "passed": [check.get("check") for check in passed],
            "failed": [check.get("check") for check in failed],
            "note": ("nothing refuted the action" if verified is True else
                     "a check failed, so the action is not accounted for" if verified is False
                     else "not enough was checked to conclude anything")}
