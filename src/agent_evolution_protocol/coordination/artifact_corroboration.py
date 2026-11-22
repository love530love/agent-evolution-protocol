"""Read an external result artifact without mistaking it for a chat reply.

An Agent may produce a file after receiving a desktop task.  If that file
contains the event id and the one-time challenge response, it is useful
*corroboration* that someone working with the task knew both values.  It is not
an original-session transcript, however: a user or any local process can write
the file, and the file does not prove who wrote it, which chat it came from, or
that the task result was read back from the client.

This module deliberately has no side effects.  In particular, it never writes
an ``AGENT_ACK`` state, updates an order, or grants downstream work permission.
Callers that need a real acceptance still have to use ``ack_protocol`` against
an attributable original-session transcript.  The returned fields make that
boundary machine-readable so a dashboard cannot accidentally round an
artifact's challenge string up into an accepted task.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


SCHEMA = "aep-artifact-corroboration-v1"
MAX_TEXT_BYTES = 2 * 1024 * 1024
_ACK = re.compile(r"^AEP-ACK[ \t]*:[ \t]*([A-Za-z0-9_-]{4,128})$")


def _expected_ack(value: Any) -> tuple[str, str]:
    """Return the canonical marker and its challenge component.

    Allowing spaces around the colon is intentional: human-authored Markdown
    often writes ``AEP-ACK: ABCD``.  Newlines and arbitrary prose are not
    accepted, so a whole assignment body cannot be passed in place of the
    one-time response marker.
    """
    match = _ACK.fullmatch(str(value or "").strip())
    if match is None:
        raise ValueError("expected_ack must be AEP-ACK:<challenge>")
    challenge = match.group(1)
    return f"AEP-ACK:{challenge}", challenge


def _event_id(value: Any) -> str:
    event_id = str(value or "").strip()
    if not event_id or len(event_id) > 256 or "\n" in event_id or "\r" in event_id:
        raise ValueError("event_id must be one non-empty line of at most 256 characters")
    return event_id


def _proofs(*, present: bool, event_present: bool, challenge_present: bool) -> dict[str, bool]:
    """State precisely what a local artifact observation does and does not prove."""
    return {
        "artifact_file_observed": present,
        "event_id_text_present": event_present,
        "challenge_text_present": challenge_present,
        # These must remain false even when both strings occur in the file.
        "original_session_bound": False,
        "original_session_ack": False,
        "original_session_readback": False,
        "agent_identity": False,
        "artifact_authorship": False,
        "task_result_readback": False,
        "delivery": False,
        "acceptance": False,
    }


def _base(*, event_id: str, expected_ack: str, artifact: Path) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "event_id": event_id,
        "expected_ack": expected_ack,
        "artifact_path": str(artifact),
        "artifact_bytes": 0,
        "artifact_sha256": "",
        "event_id_present": False,
        "challenge_present": False,
        "challenge_lines": [],
        "corroborated": False,
        "advisory_only": True,
        "may_advance": False,
        "proves": _proofs(present=False, event_present=False, challenge_present=False),
        "note": (
            "A local artifact can corroborate text only. It cannot establish an original-session "
            "ACK, agent identity, delivery, task-result readback, or permission to advance."
        ),
    }


def corroborate(artifact_path: str | Path, *, event_id: str, expected_ack: str,
                max_text_bytes: int = MAX_TEXT_BYTES) -> dict[str, Any]:
    """Inspect one UTF-8 result file for an event and challenge response.

    The output is intentionally advisory.  It can be saved alongside a receipt
    or shown to an operator, but it must never be passed to
    :func:`ack_protocol.may_advance` or used to mark an order ``AGENT_ACK``.
    ``max_text_bytes`` is bounded so an untrusted artifact cannot make a
    dashboard process an arbitrarily large file merely to search for a token.
    """
    marker, challenge = _expected_ack(expected_ack)
    event = _event_id(event_id)
    if isinstance(max_text_bytes, bool) or not isinstance(max_text_bytes, int) or max_text_bytes <= 0:
        raise ValueError("max_text_bytes must be a positive integer")
    artifact = Path(artifact_path)
    result = _base(event_id=event, expected_ack=marker, artifact=artifact)
    if not artifact.is_file():
        result.update(status="ARTIFACT_MISSING", reason="artifact_missing")
        return result
    size = artifact.stat().st_size
    result["artifact_bytes"] = size
    if size > max_text_bytes:
        result.update(status="ARTIFACT_TOO_LARGE", reason="artifact_too_large")
        result["proves"] = _proofs(present=True, event_present=False, challenge_present=False)
        return result
    try:
        payload = artifact.read_bytes()
    except OSError as exc:
        result.update(status="ARTIFACT_UNREADABLE", reason="artifact_unreadable",
                      error=type(exc).__name__)
        return result
    result["artifact_sha256"] = sha256(payload).hexdigest()
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        result.update(status="ARTIFACT_NOT_UTF8", reason="artifact_not_utf8")
        result["proves"] = _proofs(present=True, event_present=False, challenge_present=False)
        return result

    # The challenge's body is escaped before compiling it.  A nonce therefore
    # never becomes a regular expression supplied by an external artifact.
    ack_pattern = re.compile(rf"(?<![A-Za-z0-9_-])AEP-ACK[ \t]*:[ \t]*{re.escape(challenge)}(?![A-Za-z0-9_-])")
    lines = text.splitlines()
    challenge_lines = [index for index, line in enumerate(lines, start=1)
                       if ack_pattern.search(line)]
    event_present = event in text
    challenge_present = bool(challenge_lines)
    result.update(
        artifact_bytes=len(payload),
        event_id_present=event_present,
        challenge_present=challenge_present,
        challenge_lines=challenge_lines,
        corroborated=event_present and challenge_present,
        proves=_proofs(present=True, event_present=event_present,
                       challenge_present=challenge_present),
    )
    if result["corroborated"]:
        result.update(status="ARTIFACT_CORROBORATES_CHALLENGE", reason="")
    elif challenge_present:
        result.update(status="CHALLENGE_UNBOUND_TO_EVENT", reason="event_id_absent")
    elif event_present:
        result.update(status="EVENT_PRESENT_CHALLENGE_ABSENT", reason="expected_ack_absent")
    else:
        result.update(status="NO_CHALLENGE_CORROBORATION", reason="event_and_expected_ack_absent")
    return result


def main(argv: list[str] | None = None) -> int:
    """Read one artifact and print an advisory corroboration report as JSON.

    This entry point never writes an ACK, updates an order, or grants work.
    It only reads the named file so an operator can see whether a local result
    artifact at least contains the event id and the one-time challenge answer.
    """
    parser = argparse.ArgumentParser(
        description="Read-only: check whether a local artifact corroborates an event + challenge")
    parser.add_argument("artifact", help="path to the UTF-8 result file to inspect")
    parser.add_argument("--event-id", required=True, help="the order event id to look for")
    parser.add_argument("--expected-ack", required=True,
                        help="expected marker, e.g. AEP-ACK:ABCD1234")
    parser.add_argument("--max-text-bytes", type=int, default=MAX_TEXT_BYTES,
                        help=f"refuse to scan a file larger than this (default {MAX_TEXT_BYTES})")
    args = parser.parse_args(argv)
    try:
        result = corroborate(args.artifact, event_id=args.event_id,
                             expected_ack=args.expected_ack,
                             max_text_bytes=args.max_text_bytes)
    except ValueError as exc:
        parser.error(str(exc))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
