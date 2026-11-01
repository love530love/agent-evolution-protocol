"""Which session this is, in a form a later dispatch can check.

A marker check answers "does this window contain the string I expect".  That is
not the same question as "is this the session I sent to last time", and the gap
is where a message lands in the right client's wrong conversation: the string is
there because it is in the sidebar too, or because the other instance has a
similarly named session.

A fingerprint is the identity of the *session*, not of the window that happens to
show it.  It hashes the facts that a conversation cannot change without becoming
a different conversation: the agent, the executable that runs it, the window
title, and the two markers the profile uses to recognise a session and a model.

Deliberately excluded: the process id.  A restart is not a different session, and
a fingerprint that breaks on every restart is a fingerprint operators learn to
re-record without reading.  The pid is still *recorded*, as context -- "the same
session, now at pid 4242" is exactly the useful sentence after a crash.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

SCHEMA = "aep-session-fingerprint-v1"
#: Hashed, in this order, so the digest is reproducible.
IDENTITY_FIELDS = ("agent", "exe", "title", "session_marker", "model_marker")


def _normalise(value: Any) -> str:
    """Canonical form of one identity field, decided here and not by the host.

    ``os.path.normcase`` looks like the obvious tool and is the wrong one: on
    POSIX it is the identity function, and a backslash is an ordinary character.
    The same window would then produce one fingerprint on Windows and a
    different one under WSL, so a fingerprint recorded on one host would never
    match the other -- and the only reason to store a digest at all is that it
    is reproducible.  Measured: the WSL run of the suite failed on exactly this
    before the rule moved out of ``os.path``.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if "\\" in text or "/" in text:
        text = text.replace("/", "\\")
        while "\\\\" in text:
            text = text.replace("\\\\", "\\")
        text = text.rstrip("\\").lower()
    return text


def canonical(row: dict[str, Any]) -> dict[str, str]:
    return {field: _normalise(row.get(field, "")) for field in IDENTITY_FIELDS}


def compute(row: dict[str, Any]) -> str:
    """The digest, over the identity fields only."""
    payload = json.dumps(canonical(row), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def observe(*, agent: str, window: dict[str, Any], profile: dict[str, Any],
            moment: datetime | None = None) -> dict[str, Any]:
    """Build the fingerprint for a live window plus the profile it was bound to."""
    row = {
        "schema": SCHEMA,
        "agent": str(agent),
        "pid": int(window.get("pid") or 0),
        "exe": str(window.get("exe") or profile.get("exe") or ""),
        "title": str(window.get("title") or ""),
        "session_marker": str(profile.get("session_marker") or ""),
        "model_marker": str(profile.get("model_marker") or ""),
        "observed_at": (moment or datetime.now(timezone.utc)).isoformat(),
    }
    row["fingerprint"] = compute(row)
    return row


def path(coord_root, agent: str) -> Path:
    return Path(coord_root) / "clients" / "fingerprints" / f"{agent}.json"


def record(coord_root, agent: str, row: dict[str, Any],
           moment: datetime | None = None) -> dict[str, Any]:
    """Store the fingerprint an operator confirmed for this agent."""
    payload = dict(row)
    payload["schema"] = SCHEMA
    payload["agent"] = str(agent)
    payload["recorded_at"] = (moment or datetime.now(timezone.utc)).isoformat()
    payload["fingerprint"] = compute(payload)
    target = path(coord_root, agent)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, target)
    return payload


def read(coord_root, agent: str) -> dict[str, Any] | None:
    target = path(coord_root, agent)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def compare(recorded: dict[str, Any] | None, observed: dict[str, Any]) -> dict[str, Any]:
    """``match`` is ``None`` when there is nothing to compare against.

    ``None`` is not ``True``: "nobody recorded a fingerprint" and "the
    fingerprint agrees" are different facts, and collapsing them is how a check
    becomes a decoration.  The caller decides what to do with an unknown, and the
    receipt says which one it got.
    """
    if not isinstance(observed, dict):
        raise ValueError("observed fingerprint must be an object")
    if not recorded:
        return {"match": None, "diff": [], "recorded": "", "observed": observed.get("fingerprint", ""),
                "reason": "no fingerprint recorded for this agent"}
    expected = str(recorded.get("fingerprint") or compute(recorded))
    actual = str(observed.get("fingerprint") or compute(observed))
    if expected == actual:
        return {"match": True, "diff": [], "recorded": expected, "observed": actual, "reason": ""}
    left, right = canonical(recorded), canonical(observed)
    diff = [field for field in IDENTITY_FIELDS if left[field] != right[field]]
    return {"match": False, "diff": diff, "recorded": expected, "observed": actual,
            "reason": "the session identity differs in " + ", ".join(diff or ["(nothing comparable)"])}
