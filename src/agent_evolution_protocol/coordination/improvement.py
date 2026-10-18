"""Immutable, evidence-bound improvement review journal; never deploys changes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

TRANSITIONS = {"PROPOSED": {"VALIDATED", "REJECTED"}, "VALIDATED": {"REVIEWED", "REJECTED"},
               "REVIEWED": {"TRIAL", "REJECTED"}, "TRIAL": {"ADOPTED", "ROLLED_BACK"},
               "ADOPTED": {"ROLLED_BACK"}, "REJECTED": set(), "ROLLED_BACK": set()}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _folder(root, identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{24}", identity):
        raise ValueError("invalid improvement id")
    return Path(root) / "improvements" / identity


def _evidence(paths):
    if not isinstance(paths, list) or not paths:
        raise ValueError("nonempty evidence file list required")
    rows = []
    for item in paths:
        path = Path(item).resolve()
        rows.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return rows


def _save(folder, index, row):
    row = {**row, "at": datetime.now(timezone.utc).isoformat()}
    row["sha256"] = _hash(row)
    folder.mkdir(parents=True, exist_ok=True)
    # Exclusive sequence files arbitrate concurrent writers. No lost-update overwrite.
    with (folder / f"{index:06d}.json").open("x", encoding="utf-8") as stream:
        json.dump(row, stream, ensure_ascii=False, indent=2)
    return row


def status(root, identity):
    rows = []
    for path in sorted(_folder(root, identity).glob("*.json")):
        if path.name != f"{len(rows):06d}.json":
            raise ValueError("journal sequence mismatch")
        row = json.loads(path.read_text(encoding="utf-8"))
        digest = row.pop("sha256")
        if _hash(row) != digest or row["previous"] != (rows[-1]["sha256"] if rows else None):
            raise ValueError("journal integrity failure")
        rows.append({**row, "sha256": digest})
    if not rows:
        raise ValueError("improvement not found")
    return {"id": identity, "state": rows[-1]["state"], "events": rows,
            "deployment_performed": False, "identity_authenticated": False}


def propose(root, request):
    fields = {"author", "change", "acceptance", "rollback", "scope", "evidence"}
    if not isinstance(request, dict) or set(request) != fields:
        raise ValueError("proposal requires author/change/acceptance/rollback/scope/evidence")
    if any(not isinstance(request[k], str) or not request[k].strip() for k in fields - {"evidence"}):
        raise ValueError("proposal text fields must be nonempty")
    frozen = {**request, "evidence": _evidence(request["evidence"])}
    identity = _hash(frozen)[:24]
    folder = _folder(root, identity)
    if not folder.exists():
        _save(folder, 0, {"state": "PROPOSED", "actor": request["author"], "proposal": frozen, "previous": None})
    return status(root, identity)


def advance(root, identity, *, state, actor, evidence, note):
    current = status(root, identity)
    if state not in TRANSITIONS[current["state"]]:
        raise ValueError("illegal improvement transition")
    if not isinstance(actor, str) or not actor.strip() or not isinstance(note, str) or not note.strip():
        raise ValueError("actor and decision note required")
    events = current["events"]
    if state == "REVIEWED" and actor in {e["actor"] for e in events}:
        raise ValueError("reviewer must differ from author and validator")
    # Freeze evidence at every stage and reject evidence changed since earlier stages.
    for event in events if state != "ROLLED_BACK" else []:
        for record in event.get("evidence", event.get("proposal", {}).get("evidence", [])):
            if hashlib.sha256(Path(record["path"]).read_bytes()).hexdigest() != record["sha256"]:
                raise ValueError("frozen evidence changed")
    _save(_folder(root, identity), len(events), {"state": state, "actor": actor, "note": note,
          "evidence": _evidence(evidence), "previous": events[-1]["sha256"]})
    return status(root, identity)
