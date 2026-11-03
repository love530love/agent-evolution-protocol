"""One ISO-8601 parser, because ``datetime.fromisoformat`` is not one.

``datetime.fromisoformat("2026-11-02T10:00:05Z")`` returns an aware datetime on
Python 3.11 and raises ``ValueError`` on 3.10.  The library noticed and wrote
``.replace("Z", "+00:00")`` by hand in seven places; the tests did not notice,
so a literal with a trailing ``Z`` imported cleanly on three interpreters and
broke the fourth.  CI has been red since 2026-11-02 for exactly that reason.

A convention copied seven times is a convention that gets copied an eighth time
without the ``replace``.  Everything that parses a timestamp -- from a file, an
event envelope, an ACK, a dispatch envelope or a command line -- goes through
here instead, and a ratchet test refuses a bare ``fromisoformat`` on a literal
that ends in ``Z``.

The parser is deliberately a superset of what ``fromisoformat`` accepts:

* ``Z`` / ``z`` suffix, which 3.10 rejects outright;
* ``+0000`` without a colon, which 3.10 also rejects;
* a naive timestamp, optionally assumed to be UTC.

Anything else raises ``ValueError``, so callers keep the contract they already
handle.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

__all__ = ["parse_iso", "parse_iso_or_none", "to_utc"]

_ZULU = re.compile(r"[Zz]$")
_OFFSET_NO_COLON = re.compile(r"[+-]\d{4}$")


def _normalize(text: str) -> str:
    if _ZULU.search(text):
        text = text[:-1] + "+00:00"
    if _OFFSET_NO_COLON.search(text):
        text = text[:-2] + ":" + text[-2:]
    return text


def parse_iso(value: str, *, assume_utc: bool = False) -> datetime:
    """Parse an ISO-8601 timestamp.  ``ValueError`` if it is not one.

    ``assume_utc`` attaches UTC to a naive result.  Without it a naive
    timestamp stays naive, because silently inventing a timezone is how two
    processes end up eight hours apart while both believing they agree.
    """
    if not isinstance(value, str):
        raise ValueError(f"expected an ISO-8601 string, got {type(value).__name__}")
    text = value.strip()
    if not text:
        raise ValueError("expected an ISO-8601 string, got an empty string")
    parsed = datetime.fromisoformat(_normalize(text))
    if assume_utc and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_iso_or_none(value, *, assume_utc: bool = False) -> datetime | None:
    """``parse_iso`` for callers whose contract is ``None`` on bad input."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return parse_iso(value, assume_utc=assume_utc)
    except ValueError:
        return None


def to_utc(moment: datetime) -> datetime:
    """Normalise an already-parsed datetime to an aware UTC one."""
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment
