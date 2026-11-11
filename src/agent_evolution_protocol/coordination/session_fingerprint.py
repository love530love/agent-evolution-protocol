"""Which session this is, in a form a later dispatch can check.

A marker check answers "does this window contain the string I expect".  That is
not the same question as "is this the session I sent to last time", and the gap
is where a message lands in the right client's wrong conversation: the string is
there because it is in the sidebar too, or because the other instance has a
similarly named session.

A fingerprint is the identity of the *session*, not of the window that happens to
show it.  It hashes the facts that a conversation cannot change without becoming
a different conversation: the agent, the configured executable identity, the
window title, and the two markers the profile uses to recognise a session and a
model.  An explicitly trusted updater root preserves that configured executable
identity across version-directory changes; the volatile live path is retained as
``actual_exe`` for audit and never silently broadens the trust boundary.

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

from agent_evolution_protocol.coordination import process_info

SCHEMA = "aep-session-fingerprint-v1"
#: Hashed, in this order, so the digest is reproducible.
IDENTITY_FIELDS = ("agent", "exe", "title", "session_marker", "model_marker")

#: Where the conversation is being shown.  Changes when a person opens the same
#: work in another client, or renames the window.
SURFACE_FIELDS = ("agent", "exe", "title")
#: What the conversation *is*.  The only field that names it, and the one that
#: survives a move to another surface -- which is why the digest cannot be the
#: whole answer: it covers all five fields, so a surface move breaks it.
CONVERSATION_FIELD = "session_marker"
MODEL_FIELD = "model_marker"
#: Below this a marker collides with unrelated conversations ("新会话", "Hi").
MIN_MARKER_CHARS = 4

REASON_SAME_SURFACE = "SAME_SURFACE"
REASON_SURFACE_CHANGED = "SURFACE_CHANGED"
REASON_DIFFERENT_SESSION = "DIFFERENT_SESSION"
REASON_NO_RECORDED_SURFACE = "NO_RECORDED_SURFACE"
REASON_WEAK_SESSION_MARKER = "WEAK_SESSION_MARKER"


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


def merged_profile(client_row: dict[str, Any],
                   ui_row: dict[str, Any] | None = None) -> dict[str, Any]:
    """The client record plus the two markers that live in the ui profile.

    They are split across two files on purpose -- ``clients/`` names the
    *client*, ``ui_profiles/`` names the *session inside it* -- but a
    fingerprint needs both.  Measured 2026-11-02: ``coord-client fingerprint``
    recorded ``session_marker: ""`` and ``model_marker: ""`` for an agent whose
    ui profile declared both, because it handed the client record straight to
    :func:`observe`.  The digest then covered agent+exe+title only, so it could
    not tell two conversations in the same window apart -- the exact collision
    this module exists to catch, and the unit tests did not catch it because
    they build the profile by hand with the markers already in place.

    The client record wins when it carries a marker: a caller that put one
    there meant it.
    """
    row = dict(client_row or {})
    for field in IDENTITY_FIELDS[3:]:
        value = str(row.get(field) or "").strip()
        if not value and ui_row:
            value = str(ui_row.get(field) or "")
        # Always present, even when empty: a caller that indexes the key should
        # get "no marker" rather than KeyError.
        row[field] = value
    # An updater root is client identity policy, not a conversation marker.  It
    # can be declared in either record: ``clients/`` commonly owns the install
    # location while ``ui_profiles/`` is where an operator first measured the
    # versioned running path.  Carry both into observation so an approved update
    # does not falsely look like a different desktop client.
    roots: list[str] = []
    for source in (row.get("allowed_exe_roots", ()),
                   (ui_row or {}).get("allowed_exe_roots", ())):
        for value in process_info.normalise_allowed_exe_roots(source):
            if value not in roots:
                roots.append(value)
    if roots:
        row["allowed_exe_roots"] = process_info.normalise_allowed_exe_roots(roots)
    return row


def _executable_identity(window: dict[str, Any], profile: dict[str, Any]) -> tuple[str, str, str]:
    """Return stable identity path, observed path, and match mode.

    A versioned updater may replace ``...\\0.5.6\\Qoder.exe`` with
    ``...\\0.5.7\\Qoder.exe`` while the original conversation remains in the
    same application.  When that move is explicitly covered by the profile's
    same-basename updater root, fingerprint the configured logical executable
    rather than the volatile version directory.  The observed path is retained
    separately for audit.  Any unapproved path still becomes the identity and
    therefore fails comparison with the recorded client.
    """
    actual = str(window.get("exe") or "").strip()
    expected = str(profile.get("exe") or "").strip()
    verdict = process_info.executable_match(
        expected, actual, allowed_roots=profile.get("allowed_exe_roots", ()),
    )
    if verdict.get("match") and expected:
        return expected, actual, str(verdict.get("mode") or "exact")
    return actual or expected, actual, str(verdict.get("mode") or "mismatch")


def ui_profile(coord_root, agent: str) -> dict[str, Any]:
    """Read ``ui_profiles/<agent>.json`` without making its absence an error.

    An agent may be registered as a client and still have no ui profile -- that
    is a client the legion can find but not type into, and it is not this
    module's job to refuse it.  A malformed file returns ``{}`` for the same
    reason a missing one does: the fingerprint is a guard, and a guard that
    raises turns every dispatch into an outage.
    """
    path = Path(coord_root) / "ui_profiles" / f"{agent}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    try:
        data["allowed_exe_roots"] = process_info.normalise_allowed_exe_roots(
            data.get("allowed_exe_roots", ()))
    except ValueError:
        # A malformed local profile must not broaden a fingerprint policy.  The
        # caller will see the absence of profile markers and hold rather than
        # derive a stable session identity from a whole-volume trust root.
        return {}
    return data


def observe(*, agent: str, window: dict[str, Any], profile: dict[str, Any],
            moment: datetime | None = None) -> dict[str, Any]:
    """Build the fingerprint for a live window plus the profile it was bound to."""
    executable, actual_exe, exe_match_mode = _executable_identity(window, profile)
    row = {
        "schema": SCHEMA,
        "agent": str(agent),
        "pid": int(window.get("pid") or 0),
        "exe": executable,
        "actual_exe": actual_exe,
        "exe_match_mode": exe_match_mode,
        "title": str(window.get("title") or ""),
        "session_marker": str(profile.get("session_marker") or ""),
        "model_marker": str(profile.get("model_marker") or ""),
        "observed_at": (moment or datetime.now(timezone.utc)).isoformat(),
    }
    row["fingerprint"] = compute(row)
    return row


def path(coord_root, agent: str) -> Path:
    return Path(coord_root) / "clients" / "fingerprints" / f"{agent}.json"


def directory(coord_root) -> Path:
    return Path(coord_root) / "clients" / "fingerprints"


def all_recorded(coord_root) -> list[dict[str, Any]]:
    """Every fingerprint on disk, with the agent name filled in from the filename.

    A row whose ``agent`` disagrees with its filename is returned as-is rather
    than repaired: the file is named after the agent it was recorded for, so a
    mismatch is evidence of a hand edit and hiding it would erase the clue.
    """
    rows: list[dict[str, Any]] = []
    folder = directory(coord_root)
    if not folder.is_dir():
        return rows
    for item in sorted(folder.glob("*.json")):
        try:
            payload = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            payload.setdefault("agent", item.stem)
            rows.append(payload)
    return rows


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


def marker(row: dict[str, Any]) -> str:
    """The conversation marker, normalised the same way the digest normalises it."""
    return canonical(row)[CONVERSATION_FIELD]


def same_surface(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Are these two observations of the same window on the same client?"""
    a, b = canonical(left), canonical(right)
    return all(a[field] == b[field] for field in SURFACE_FIELDS)


def continuity(recorded: dict[str, Any] | None,
               observed: dict[str, Any]) -> dict[str, Any]:
    """Can a delivery recorded on one surface continue on this one?

    This is the cross-surface question, and the digest cannot answer it: the
    digest covers agent, exe and title, so the same conversation opened in a
    different client produces a different digest and reads as a different
    session.  Splitting the fields is what makes "same conversation, new
    surface" expressible at all.

    Four answers, and the third is the one worth keeping:

    * ``True`` -- the marker matches.  ``same_surface`` says whether anything
      else changed, and ``model_changed`` is a note rather than a refusal
      because switching models does not start a new conversation.
    * ``False`` -- both sides name a conversation and they are different ones.
    * ``None`` -- nothing recorded, or the marker is too short to be an
      identity.  ``None`` is not ``True``: two conversations in the same client
      with no marker are indistinguishable, and pretending otherwise is how a
      message lands in the wrong thread.
    """
    if not isinstance(observed, dict):
        raise ValueError("observed fingerprint must be an object")
    if not recorded:
        return {"continues": None, "same_surface": False, "model_changed": False,
                "reason": REASON_NO_RECORDED_SURFACE,
                "note": "no surface was recorded for this conversation"}

    left, right = canonical(recorded), canonical(observed)
    expected = marker(recorded)
    actual = marker(observed)
    if len(expected) < MIN_MARKER_CHARS or len(actual) < MIN_MARKER_CHARS:
        return {"continues": None, "same_surface": same_surface(recorded, observed),
                "model_changed": False, "reason": REASON_WEAK_SESSION_MARKER,
                "note": f"a session marker shorter than {MIN_MARKER_CHARS} characters "
                        "does not separate one conversation from another"}

    model_changed = (left[MODEL_FIELD] != right[MODEL_FIELD])
    if expected != actual:
        return {"continues": False, "same_surface": same_surface(recorded, observed),
                "model_changed": model_changed, "reason": REASON_DIFFERENT_SESSION,
                "note": "both sides name a conversation, and they are different ones",
                "recorded_marker": recorded.get(CONVERSATION_FIELD, ""),
                "observed_marker": observed.get(CONVERSATION_FIELD, "")}

    on_same_surface = same_surface(recorded, observed)
    return {"continues": True, "same_surface": on_same_surface,
            "model_changed": model_changed,
            "reason": REASON_SAME_SURFACE if on_same_surface else REASON_SURFACE_CHANGED,
            "note": "" if on_same_surface else
                    "the conversation moved to another surface; the marker still names it",
            "surface_differs": [field for field in SURFACE_FIELDS
                                if left[field] != right[field]]}


def group_by_conversation(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every surface currently showing each conversation.

    Grouping is by marker, so a conversation observed on two clients appears
    once with two surfaces.  Rows with a weak marker are returned in their own
    group keyed by fingerprint instead of being merged with everything else that
    happens to have an empty marker -- merging them would produce one group that
    claims to be every unlabelled conversation at once.
    """
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = marker(row)
        usable = len(key) >= MIN_MARKER_CHARS
        bucket_key = key if usable else "unidentified:" + str(row.get("fingerprint", ""))
        bucket = groups.setdefault(bucket_key, {
            "conversation": row.get(CONVERSATION_FIELD, "") if usable else "",
            "identified": usable, "surfaces": [], "fingerprints": []})
        bucket["surfaces"].append({field: row.get(field, "") for field in SURFACE_FIELDS})
        bucket["fingerprints"].append(row.get("fingerprint", ""))
    return sorted(groups.values(), key=lambda item: (not item["identified"], item["conversation"]))
