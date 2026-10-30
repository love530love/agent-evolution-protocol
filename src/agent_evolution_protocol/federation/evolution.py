"""Strategy library: learn from recoveries, promote only what keeps working.

The library separates three states so "we learned something" cannot be confused
with "we ship it":

``proposed``
    Recorded after one recovery, not yet trusted.

``promoted``
    Reached the success threshold with no failures in its record; new
    workspaces inherit it.

``retired``
    Kept for audit after it stopped working, so the same idea is not
    re-proposed from scratch.

Promotion is a pure function of the recorded evidence. Nothing here decides
that a strategy "feels" better.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from .store import atomic_write, read_json, stamp


LIBRARY_SCHEMA = "aep-federation-strategy-library-v1"
STATES = ("proposed", "promoted", "retired")
DEFAULT_PROMOTION = {"min_successes": 2, "max_failures": 0}


def new_library():
    return {"schema": LIBRARY_SCHEMA, "updated_at": "", "strategies": {}}


def _id(name, params):
    body = json.dumps({"name": name, "params": params}, ensure_ascii=False, sort_keys=True)
    return "S-" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def propose(library, *, name, tags, params=None, moment=None):
    if not isinstance(library, dict) or library.get("schema") != LIBRARY_SCHEMA:
        raise ValueError("unknown strategy library schema")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("strategy name is required")
    if not isinstance(tags, (list, tuple)) or not tags:
        raise ValueError("at least one trigger tag is required")
    params = params or {}
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    strategy_id = _id(name.strip(), params)
    existing = library["strategies"].get(strategy_id)
    if existing:
        return existing
    row = {"id": strategy_id, "name": name.strip(),
           "tags": sorted({str(tag).strip() for tag in tags if str(tag).strip()}),
           "params": dict(params), "status": "proposed", "successes": 0, "failures": 0,
           "created_at": stamp(moment), "promoted_at": "", "history": []}
    library["strategies"][strategy_id] = row
    library["updated_at"] = stamp(moment)
    return row


def record(library, strategy_id, *, outcome, workspace_id, note="", moment=None):
    """Append one verified outcome; never edits earlier history."""
    if outcome not in {"success", "failure"}:
        raise ValueError("outcome must be success or failure")
    row = library["strategies"].get(strategy_id)
    if row is None:
        raise ValueError("unknown strategy")
    row[("successes" if outcome == "success" else "failures")] += 1
    row["history"].append({"at": stamp(moment), "outcome": outcome,
                           "workspace_id": workspace_id, "note": str(note)[:500]})
    library["updated_at"] = stamp(moment)
    return row


def promote(library, strategy_id, *, min_successes=None, max_failures=None, moment=None):
    """Promote only when the recorded evidence clears the bar."""
    row = library["strategies"].get(strategy_id)
    if row is None:
        raise ValueError("unknown strategy")
    bar = dict(DEFAULT_PROMOTION)
    if min_successes is not None:
        bar["min_successes"] = int(min_successes)
    if max_failures is not None:
        bar["max_failures"] = int(max_failures)
    if row["successes"] < bar["min_successes"]:
        raise ValueError(f"NOT_ENOUGH_EVIDENCE: {row['successes']}/{bar['min_successes']} successes")
    if row["failures"] > bar["max_failures"]:
        raise ValueError(f"TOO_MANY_FAILURES: {row['failures']} > {bar['max_failures']}")
    row["status"] = "promoted"
    row["promoted_at"] = stamp(moment)
    library["updated_at"] = row["promoted_at"]
    return row


def retire(library, strategy_id, *, reason, moment=None):
    row = library["strategies"].get(strategy_id)
    if row is None:
        raise ValueError("unknown strategy")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("retire reason is required")
    row["status"] = "retired"
    row["history"].append({"at": stamp(moment), "outcome": "retired", "reason": reason[:500]})
    library["updated_at"] = stamp(moment)
    return row


def inherit(global_library, workspace_id):
    """Copy promoted strategies into a new workspace's own library."""
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise ValueError("workspace_id is required")
    local = new_library()
    for strategy_id, row in sorted(global_library.get("strategies", {}).items()):
        if row.get("status") != "promoted":
            continue
        clone = json.loads(json.dumps(row))
        clone["inherited_from"] = strategy_id
        clone["inherited_at"] = datetime.now(timezone.utc).isoformat()
        local["strategies"][strategy_id] = clone
    local["updated_at"] = datetime.now(timezone.utc).isoformat()
    return local


def save(path, library):
    if library.get("schema") != LIBRARY_SCHEMA:
        raise ValueError("unknown strategy library schema")
    atomic_write(path, library)
    return path


def load(path):
    library = read_json(path)
    if library is None:
        return new_library()
    if library.get("schema") != LIBRARY_SCHEMA:
        raise ValueError("unknown strategy library schema")
    return library
