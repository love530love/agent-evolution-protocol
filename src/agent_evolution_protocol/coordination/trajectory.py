"""A run that can be checked afterwards, and replayed without re-firing it.

CLI-Anything's ``trajectory.json`` binds each command to its result so the
record cannot be rewritten into a different story after the fact.  AEP already
keeps receipts, but they are per-attempt and scattered across ``commander/``;
what was missing is the ordered list that says "this argv produced this result",
with the binding strong enough that editing either half is detectable.

Two rules are load-bearing:

1. **The digest is only worth having if it can be re-derived.**  A step stores
   the argv itself plus its digest, and a pointer to the result plus its digest,
   so :func:`verify` recomputes instead of trusting.  A digest that is stored
   but never recomputed is a checksum-shaped decoration.
2. **Replay is not re-fire.**  :func:`replay_plan` returns only the steps whose
   command is ``read-only`` according to the generated skill manifest, and
   refuses the rest by name.  A trajectory is an audit artefact; letting it
   drive a send would turn "reproduce the run" into "repeat the side effects".
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

from agent_evolution_protocol.coordination import skill_manifest

SCHEMA = "aep-trajectory-v1"
STEP_SCHEMA = "aep-trajectory-step-v1"

#: Windows refuses these as file names whatever the extension.
RESERVED_IDS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
                *(f"LPT{i}" for i in range(10))}
#: Enough of the result to recognise it in a listing; the digest covers the rest.
EXCERPT_CHARS = 400


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_of(value: Any) -> str:
    return "sha256:" + sha256(_canonical(value).encode("utf-8")).hexdigest()


def valid_id(value: str) -> str:
    """A trajectory id that is safe as a filename on this host.

    ``CON`` and friends are rejected because ``CON.json`` is still a device name
    on Windows, and the failure then looks like an empty trajectory rather than
    a bad identifier.
    """
    text = str(value or "").strip()
    if not text or len(text) > 96:
        raise ValueError("a trajectory id must be 1..96 characters")
    if any(character in text for character in "/\\:*?\"<>|") or text.upper() in RESERVED_IDS:
        raise ValueError(f"trajectory id is not safe as a filename: {value!r}")
    if set(text) == {"."}:
        # ".." becomes "...json", which is a legal file name and an unusable id.
        raise ValueError(f"trajectory id cannot be dots only: {value!r}")
    return text


def directory(coord_root) -> Path:
    return Path(coord_root) / "trajectories"


def path(coord_root, trajectory_id: str) -> Path:
    return directory(coord_root) / f"{valid_id(trajectory_id)}.json"


def command_of(argv: list[str], *, known: set[str] | None = None) -> str:
    """Which command this argv invokes, found by name and not by position.

    ``--coord-root`` takes a value, so "the first token that is not a flag" is
    wrong: it would answer the path.  Matching against the real command names is
    the only version that does not need a copy of the global flag list here.
    """
    if known is None:
        known = {row["name"] for row in skill_manifest.build()["commands"]}
    for token in argv or []:
        if token in known:
            return token
    return ""


def start(coord_root, *, trajectory_id: str, agent: str = "", task: str = "",
          surface: str = "", note: str = "") -> dict[str, Any]:
    """Open a trajectory, or return the one already open under this id.

    Re-opening is not an error: a run that spans two ticks should accumulate
    steps, and a fresh id per tick would make the ordering meaningless.
    """
    existing = load(coord_root, trajectory_id)
    if existing is not None:
        return existing
    payload = {
        "schema": SCHEMA,
        "trajectory_id": valid_id(trajectory_id),
        "agent": str(agent or ""),
        "task": str(task or ""),
        "surface": str(surface or ""),
        "note": str(note or ""),
        "created_at": _now(),
        "steps": [],
    }
    _write(coord_root, payload)
    return payload


def load(coord_root, trajectory_id: str) -> dict[str, Any] | None:
    target = path(coord_root, trajectory_id)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("steps"), list):
        return None
    return payload


def _write(coord_root, payload: dict[str, Any]) -> None:
    target = path(coord_root, payload["trajectory_id"])
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, target)


def record(coord_root, *, trajectory_id: str, argv: list[str], exit_code: int = 0,
           result: Any = None, result_ref: str = "", note: str = "",
           known: set[str] | None = None) -> dict[str, Any]:
    """Append one step, binding the argv to the result it produced.

    ``result_ref`` is a path to the stored result rather than the result itself:
    receipts already live in their own files, and copying them here would create
    a second copy that can drift from the first.  The digest is taken over
    whatever is passed, so an inlined result and a referenced one are both
    checkable.
    """
    trajectory = start(coord_root, trajectory_id=trajectory_id)
    argv = [str(token) for token in (argv or [])]
    command = command_of(argv, known=known)
    risk = skill_manifest.risk_of(command) if command else skill_manifest.MUTATING
    step: dict[str, Any] = {
        "schema": STEP_SCHEMA,
        "index": len(trajectory["steps"]),
        "command": command,
        "risk": risk,
        "argv": argv,
        "argv_digest": digest_of(argv),
        "exit_code": int(exit_code),
        "recorded_at": _now(),
        "note": str(note or ""),
    }
    if result_ref:
        step["result_ref"] = str(result_ref)
    if result is not None:
        step["result_digest"] = digest_of(result)
        step["result_excerpt"] = _canonical(result)[:EXCERPT_CHARS]
    elif result_ref:
        step["result_digest"] = _file_digest(result_ref)
    trajectory["steps"].append(step)
    trajectory["updated_at"] = step["recorded_at"]
    _write(coord_root, trajectory)
    return step


def _file_digest(reference: str) -> str:
    """Digest of a referenced result, over its *value* rather than its bytes.

    Parsing first is what makes an inlined result and a referenced one agree:
    the same JSON written with different whitespace is the same result, and a
    digest that depends on formatting would report a receipt as changed because
    something reformatted it.  A file that is not JSON falls back to its text,
    which is still a binding, just a coarser one.

    Empty rather than raising: a trajectory written on one host may be read on
    another where the receipt path does not exist, and that is a reportable
    state rather than a crash.
    """
    try:
        text = Path(reference).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    try:
        return digest_of(json.loads(text))
    except ValueError:
        return digest_of(text)


def verify(trajectory: dict[str, Any]) -> dict[str, Any]:
    """Recompute every binding.  Nothing here trusts what was stored.

    Three distinct failures, because they need different responses: an argv that
    no longer hashes to its digest means the file was edited; a result whose file
    is gone means the evidence is missing; a result whose file changed means the
    two halves no longer describe the same run.
    """
    steps = trajectory.get("steps") if isinstance(trajectory, dict) else None
    if not isinstance(steps, list):
        return {"ok": False, "steps": 0, "tampered": [], "missing": [], "changed": [],
                "reason": "NOT_A_TRAJECTORY"}
    tampered, missing, changed = [], [], []
    for step in steps:
        index = step.get("index")
        if digest_of(step.get("argv") or []) != step.get("argv_digest"):
            tampered.append(index)
            continue
        reference = step.get("result_ref")
        if not reference:
            continue
        recomputed = _file_digest(reference)
        if not recomputed:
            missing.append(index)
        elif recomputed != step.get("result_digest"):
            changed.append(index)
    reasons = [name for name, rows in (("ARGV_TAMPERED", tampered),
                                       ("RESULT_MISSING", missing),
                                       ("RESULT_CHANGED", changed)) if rows]
    return {"schema": "aep-trajectory-verify-v1", "ok": not reasons, "steps": len(steps),
            "tampered": tampered, "missing": missing, "changed": changed,
            "reasons": reasons}


def replay_plan(trajectory: dict[str, Any]) -> dict[str, Any]:
    """The argv list that would reproduce the read-only part of the run.

    Mutating steps are refused by name with their risk, not silently dropped:
    "the replay has four steps instead of nine" is only safe if the other five
    are accounted for in the same object.
    """
    steps = trajectory.get("steps") if isinstance(trajectory, dict) else None
    if not isinstance(steps, list):
        return {"schema": "aep-trajectory-replay-v1", "replayable": [], "refused": [],
                "reason": "NOT_A_TRAJECTORY"}
    replayable, refused = [], []
    for step in steps:
        command = str(step.get("command") or "")
        risk = skill_manifest.risk_of(command) if command else skill_manifest.MUTATING
        argv = [str(token) for token in (step.get("argv") or [])]
        if risk == skill_manifest.READ_ONLY and argv:
            replayable.append({"index": step.get("index"), "command": command, "argv": argv})
        else:
            refused.append({"index": step.get("index"), "command": command, "risk": risk,
                            "reason": "NOT_READ_ONLY" if argv else "NO_ARGV"})
    return {"schema": "aep-trajectory-replay-v1", "replayable": replayable,
            "refused": refused,
            "note": "a replay reproduces the reads; re-running a write is a new decision"}
