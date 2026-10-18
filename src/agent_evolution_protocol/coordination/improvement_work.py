"""Fenced claims for improvement handoffs; no agent wakeup or implicit execution."""
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from . import improvement
from .battle_orders import _atomic_json, _task_writer_lock

NEXT = {"PROPOSED": ("validate", "VALIDATED"), "VALIDATED": ("review", "REVIEWED"),
        "REVIEWED": ("trial", "TRIAL"), "TRIAL": ("assess", "ADOPTED")}


def _now():
    return datetime.now(timezone.utc).timestamp()


def _path(root, identity):
    improvement._folder(root, identity)  # Validate before constructing a path.
    return Path(root) / "improvement_claims" / f"{identity}.json"


def claim(root, identity, *, actor, ttl=300):
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor required")
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= 3600:
        raise ValueError("ttl must be 1..3600 seconds")
    with _task_writer_lock(Path(root), "improvement-handoff"):
        current = improvement.status(root, identity)
        if current["state"] not in NEXT:
            raise ValueError("no pending work for this state")
        if current["state"] == "VALIDATED" and actor in {e["actor"] for e in current["events"]}:
            raise ValueError("review requires an independent actor")
        path = _path(root, identity)
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        head = current["events"][-1]["sha256"]
        now = _now()
        if old.get("active") and old.get("head") == head and old["expires"] > now:
            raise ValueError("work already claimed; wait for release or expiry")
        row = {"id": identity, "actor": actor, "token": uuid4().hex, "head": head,
               "action": NEXT[current["state"]][0], "target": NEXT[current["state"]][1],
               "expires": now + ttl, "active": True, "previous_token": old.get("token")}
        _atomic_json(path, row)
        return {**row, "agent_awakened": False, "execution_started": False}


def finish(root, identity, *, actor, token, evidence=None, note=None, release=False):
    with _task_writer_lock(Path(root), "improvement-handoff"):
        path = _path(root, identity)
        row = json.loads(path.read_text(encoding="utf-8"))
        current = improvement.status(root, identity)
        if not row["active"] or row["actor"] != actor or row["token"] != token:
            raise ValueError("claim ownership mismatch")
        if row["expires"] <= _now() or row["head"] != current["events"][-1]["sha256"]:
            raise ValueError("claim expired or proposal changed; stale worker fenced")
        if not release:
            current = improvement.advance(root, identity, state=row["target"], actor=actor,
                                          evidence=evidence, note=note)
        _atomic_json(path, {**row, "active": False, "result": "RELEASED" if release else "COMPLETED"})
        return {"id": identity, "state": current["state"], "released": release,
                "deployment_performed": False}


def pending(root):
    rows = []
    for folder in sorted((Path(root) / "improvements").glob("*")):
        if not folder.is_dir():
            continue
        try:
            current = improvement.status(root, folder.name)
            if current["state"] not in NEXT:
                continue
            path = _path(root, folder.name)
            lease = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            active = bool(lease.get("active") and lease.get("expires", 0) > _now()
                          and lease.get("head") == current["events"][-1]["sha256"])
            rows.append({"id": folder.name, "state": current["state"], "action": NEXT[current["state"]][0],
                         "available": not active, "owner": lease.get("actor") if active else None})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            rows.append({"id": folder.name, "available": False, "error": str(exc)})
    return {"work": rows, "agent_awakened": False}
