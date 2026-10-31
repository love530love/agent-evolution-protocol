"""Cross-claim write-scope conflicts and deterministic arbitration.

A task claim protects a task ID, not a directory tree. Two different task IDs
can therefore declare overlapping ``paths`` and both believe they own the same
file. Read-side helpers report that condition and propose a resolution. The
write-side ``claim`` and ``renew`` helpers only create or extend the caller's
own coordination record; they never touch scoped files or take over another
task's claim.

Scope rules:

* Paths are repository-relative POSIX strings. Absolute paths, drive letters and
  ``..`` segments are rejected as ``invalid_scope`` so a claim cannot silently
  escape the workspace.
* Two scopes overlap when they are equal, or when one is a directory prefix of
  the other (``src/a`` overlaps ``src/a/b.py`` but not ``src/ab``).
* A claim without ``paths`` is a task-level claim and takes no filesystem scope;
  it is reported as ``task_only`` and never produces path conflicts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path, PurePosixPath
import re


SEVERITY_ORDER = {"blocking": 0, "warning": 1, "info": 2}
DEFAULT_STALE_HOURS = 12.0
DEFAULT_CLAIM_TTL_HOURS = 48.0
_DRIVE = re.compile(r"^[A-Za-z]:")
_AGENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_TASK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def _now(now):
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if isinstance(now, str):
        parsed = datetime.fromisoformat(now.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise TypeError("now must be a datetime, ISO string or None")


def _stamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def normalize(path):
    """Return a canonical relative POSIX path, or raise ValueError.

    Raises rather than guessing: a scope that cannot be normalized must be
    reported, not silently rewritten into something that looks safe.
    """
    if not isinstance(path, str):
        raise ValueError("scope path must be a string")
    text = path.strip().replace("\\", "/")
    if not text:
        raise ValueError("scope path is empty")
    if text.startswith("/") or _DRIVE.match(text):
        raise ValueError("scope path must be repository-relative")
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if not parts:
        raise ValueError("scope path is empty")
    if any(part == ".." for part in parts):
        raise ValueError("scope path must not traverse upward")
    return "/".join(parts)


def overlaps(left, right):
    """True when two canonical scopes address at least one common path."""
    a, b = PurePosixPath(left.lower()), PurePosixPath(right.lower())
    if a == b:
        return True
    return a in b.parents or b in a.parents


def _read_claims(coord_root):
    folder = Path(coord_root) / "claims"
    rows = []
    if not folder.is_dir():
        return rows
    for owner in sorted(folder.glob("*/owner.json")):
        try:
            data = json.loads(owner.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            rows.append({"task": owner.parent.name, "agent": "", "at": "", "paths": [],
                         "unreadable": f"{type(exc).__name__}: {exc}"})
            continue
        if not isinstance(data, dict):
            rows.append({"task": owner.parent.name, "agent": "", "at": "", "paths": [],
                         "unreadable": "owner.json is not an object"})
            continue
        paths = data.get("paths")
        rows.append({"task": data.get("task") or owner.parent.name,
                     "agent": data.get("agent") or "", "at": data.get("at") or "",
                     "expires_at": data.get("expires_at") or "",
                     "paths": [p for p in paths if isinstance(p, str)] if isinstance(paths, list) else []})
    return rows


def _normalize_claim(row, ttl_hours=DEFAULT_CLAIM_TTL_HOURS):
    """Split a claim into valid scopes, invalid scopes and an expiry moment.

    Legacy claims carry no ``expires_at``; they inherit ``at + ttl_hours`` and
    are flagged ``expiry_assumed`` so nobody mistakes a computed deadline for a
    declared one.
    """
    valid, invalid = [], []
    for raw in row["paths"]:
        try:
            valid.append(normalize(raw))
        except ValueError as exc:
            invalid.append({"path": raw, "reason": str(exc)})
    explicit = _stamp(row.get("expires_at") or "")
    at = _stamp(row["at"])
    expiry = explicit or (at + timedelta(hours=ttl_hours) if at else None)
    return {"task": row["task"], "agent": row["agent"], "at": row["at"],
            "scopes": sorted(set(valid)), "invalid": invalid,
            "task_only": not row["paths"], "unreadable": row.get("unreadable", ""),
            "expires_at": expiry.isoformat() if expiry else "",
            "expiry_assumed": explicit is None and expiry is not None}


def _is_expired(claim, moment):
    expiry = _stamp(claim.get("expires_at") or "")
    return expiry is not None and expiry <= moment


def analyze(coord_root, *, now=None, stale_after_hours=DEFAULT_STALE_HOURS,
            ttl_hours=DEFAULT_CLAIM_TTL_HOURS):
    """Return every conflict a writer must resolve before touching a file.

    Deterministic: the same coordination directory always yields the same
    report, ordered by severity then task names then path. An expired claim
    stops blocking other tasks (that is what a lease deadline means) but is
    always reported so the owner can renew or release it.
    """
    moment = _now(now)
    if not isinstance(stale_after_hours, (int, float)) or stale_after_hours <= 0:
        raise ValueError("stale_after_hours must be a positive number")
    if not isinstance(ttl_hours, (int, float)) or ttl_hours <= 0:
        raise ValueError("ttl_hours must be a positive number")
    claims = [_normalize_claim(row, ttl_hours) for row in _read_claims(coord_root)]
    conflicts = []

    for claim in claims:
        if claim["unreadable"]:
            conflicts.append({"kind": "unreadable_claim", "severity": "blocking",
                              "tasks": [claim["task"]], "path": "", "agents": [claim["agent"]],
                              "detail": claim["unreadable"]})
        for item in claim["invalid"]:
            conflicts.append({"kind": "invalid_scope", "severity": "blocking",
                              "tasks": [claim["task"]], "path": item["path"],
                              "agents": [claim["agent"]], "detail": item["reason"]})
        at = _stamp(claim["at"])
        if at is not None and (moment - at).total_seconds() > stale_after_hours * 3600:
            age = round((moment - at).total_seconds() / 3600, 1)
            conflicts.append({"kind": "stale_claim", "severity": "warning",
                              "tasks": [claim["task"]], "path": "",
                              "agents": [claim["agent"]],
                              "detail": f"claimed {age}h ago, threshold {stale_after_hours}h"})
        if _is_expired(claim, moment):
            expiry = _stamp(claim["expires_at"])
            age = round((moment - expiry).total_seconds() / 3600, 1)
            conflicts.append({"kind": "expired_claim", "severity": "warning",
                              "tasks": [claim["task"]], "path": "",
                              "agents": [claim["agent"]],
                              "detail": (f"lease expired {age}h ago; "
                                         f"expiry {'assumed from at+ttl' if claim['expiry_assumed'] else 'declared'}"),
                              "action": "owner should renew or release"})

    for index, left in enumerate(claims):
        for right in claims[index + 1:]:
            if left["task"] == right["task"]:
                conflicts.append({"kind": "duplicate_task_id", "severity": "blocking",
                                  "tasks": [left["task"], right["task"]], "path": "",
                                  "agents": [left["agent"], right["agent"]],
                                  "detail": "two claim records share one task ID"})
                continue
            if _is_expired(left, moment) or _is_expired(right, moment):
                continue
            for a in left["scopes"]:
                for b in right["scopes"]:
                    if not overlaps(a, b):
                        continue
                    winner, loser = _arbitrate(left, right, a, b)
                    winner_claim = left if left["task"] == winner else right
                    loser_claim = right if winner_claim is left else left
                    conflicts.append({
                        "kind": "path_overlap", "severity": "blocking",
                        "tasks": [left["task"], right["task"]],
                        "agents": [left["agent"], right["agent"]],
                        "path": a if a == b else f"{a} <> {b}",
                        "detail": "both claims may write the same path",
                        "resolution": {"winner": winner, "loser": loser,
                                       "winner_scopes": winner_claim["scopes"],
                                       "loser_scopes": loser_claim["scopes"],
                                       "action": "loser must narrow scope before writing",
                                       "remaining": _remaining(loser, claims, a, b)},
                    })

    conflicts.sort(key=lambda item: (SEVERITY_ORDER.get(item["severity"], 9),
                                     item["kind"], tuple(item["tasks"]), item["path"]))
    blocking = [c for c in conflicts if c["severity"] == "blocking"]
    return {
        "schema": "agent-evolution-claim-conflicts-v1",
        "checked_at": moment.isoformat(),
        "coord_root": str(Path(coord_root)),
        "claims": len(claims),
        "scoped_claims": sum(1 for c in claims if c["scopes"]),
        "task_only_claims": sum(1 for c in claims if c["task_only"]),
        "conflicts": conflicts,
        "blocking": len(blocking),
        "status": "CONFLICT" if blocking else "CLEAN",
        "write_allowed": not blocking,
        "ttl_hours": ttl_hours,
        "expired_claims": [claim["task"] for claim in claims if _is_expired(claim, moment)],
        "mutated_claims": False,
    }


def _arbitrate(left, right, a, b):
    """Earlier claim keeps the contested path; ties break on task ID then agent."""
    keys = []
    for claim in (left, right):
        at = _stamp(claim["at"])
        keys.append(((at or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
                     claim["task"], claim["agent"]))
    return (left["task"], right["task"]) if keys[0] <= keys[1] else (right["task"], left["task"])


def _remaining(loser_task, claims, a, b):
    """Scopes the losing claim may keep once the contested path is removed."""
    claim = next(item for item in claims if item["task"] == loser_task)
    keep = []
    for scope in claim["scopes"]:
        if overlaps(scope, a) or overlaps(scope, b):
            continue
        keep.append(scope)
    return keep


def gate(coord_root, *, agent, task, paths, now=None, stale_after_hours=DEFAULT_STALE_HOURS,
         ttl_hours=DEFAULT_CLAIM_TTL_HOURS):
    """Pre-write check for one agent's intended scope.

    Compares the requested scope against every live claim, including the common
    case where this task has no claim record yet. An unreadable claim makes the
    ownership map untrustworthy, so it blocks instead of guessing. Expired
    leases do not block, but they are listed in ``ignored_expired`` rather than
    disappearing. It does not create the claim.
    """
    if not isinstance(agent, str) or not agent.strip():
        raise ValueError("agent is required")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task is required")
    wanted = sorted({normalize(path) for path in paths})
    if not wanted:
        raise ValueError("at least one scope path is required")
    moment = _now(now)
    report = analyze(coord_root, now=moment, stale_after_hours=stale_after_hours, ttl_hours=ttl_hours)
    blocking = [conflict for conflict in report["conflicts"]
                if conflict["severity"] == "blocking"
                and conflict["kind"] in {"unreadable_claim", "duplicate_task_id"}]
    ignored = []
    self_overlaps = []
    for claim in _normalize_claim_all(coord_root, ttl_hours):
        if claim["task"] == task:
            continue
        hits = [{"wanted": scope, "claimed": claimed}
                for scope in wanted for claimed in claim["scopes"]
                if overlaps(scope, claimed)]
        if not hits:
            continue
        if _is_expired(claim, moment):
            ignored.append({"task": claim["task"], "agent": claim["agent"],
                            "expires_at": claim["expires_at"], "overlaps": hits})
            continue
        if claim["agent"] == agent:
            # The same agent already covers this path under another task. That
            # is one writer holding two labels, not a conflict: blocking it
            # would force an agent to fragment its own work into a new task
            # every time it continues, which is how a protocol teaches people
            # to bypass it.
            self_overlaps.append({"task": claim["task"], "agent": claim["agent"],
                                  "overlaps": hits})
            continue
        blocking.append({"kind": "path_overlap", "severity": "blocking",
                         "tasks": [task, claim["task"]],
                         "agents": [agent, claim["agent"]],
                         "path": hits[0]["claimed"], "detail": "another claim already covers this path",
                         "overlaps": hits})
    return {"schema": "agent-evolution-claim-gate-v1", "agent": agent, "task": task,
            "scopes": wanted, "allowed": not blocking, "blocking": blocking,
            "ignored_expired": ignored, "self_overlaps": self_overlaps,
            "status": "ALLOWED" if not blocking else "BLOCKED",
            "claim_created": False}


def _normalize_claim_all(coord_root, ttl_hours=DEFAULT_CLAIM_TTL_HOURS):
    return [_normalize_claim(row, ttl_hours) for row in _read_claims(coord_root)]


def claim(coord_root, *, agent, task, paths, ttl_hours=DEFAULT_CLAIM_TTL_HOURS, now=None):
    """Gate first, then create the claim atomically with a lease deadline.

    This is the write-side counterpart of :func:`gate`: it refuses to create a
    claim whose scope already belongs to a live lease, and it stamps
    ``expires_at`` so the claim cannot block the workspace forever. It never
    takes over an existing task ID and never overwrites another owner.
    """
    if not isinstance(agent, str) or not _AGENT.fullmatch(agent.strip()):
        raise ValueError("agent must be a stable agent ID")
    if not isinstance(task, str) or not _TASK.fullmatch(task.strip()):
        raise ValueError("task must be a stable task ID")
    if not isinstance(ttl_hours, (int, float)) or ttl_hours <= 0:
        raise ValueError("ttl_hours must be positive")
    agent, task = agent.strip(), task.strip()
    wanted = sorted({normalize(path) for path in paths})
    if not wanted:
        raise ValueError("at least one scope path is required")
    moment = _now(now)
    verdict = gate(coord_root, agent=agent, task=task, paths=wanted, now=moment, ttl_hours=ttl_hours)
    if not verdict["allowed"]:
        return {"schema": "agent-evolution-claim-result-v1", "claimed": False,
                "reason": "scope_conflict", "task": task, "agent": agent,
                "blocking": verdict["blocking"], "claim_created": False}

    folder = Path(coord_root) / "claims" / task
    try:
        folder.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        owner = folder / "owner.json"
        return {"schema": "agent-evolution-claim-result-v1", "claimed": False,
                "reason": "task_already_claimed", "task": task, "agent": agent,
                "owner": json.loads(owner.read_text(encoding="utf-8")) if owner.is_file() else None,
                "claim_created": False}
    payload = {"task": task, "agent": agent, "at": moment.isoformat(), "paths": wanted,
               "expires_at": (moment + timedelta(hours=ttl_hours)).isoformat(),
               "gate": {"status": verdict["status"],
                        "ignored_expired": [row["task"] for row in verdict["ignored_expired"]]}}
    try:
        temporary = folder / "owner.json.partial"
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(folder / "owner.json")
    except BaseException:
        for leftover in (folder / "owner.json.partial", folder / "owner.json"):
            if leftover.exists():
                leftover.unlink()
        folder.rmdir()
        raise
    return {"schema": "agent-evolution-claim-result-v1", "claimed": True, **payload,
            "claim_created": True}


def renew(coord_root, *, agent, task, ttl_hours=DEFAULT_CLAIM_TTL_HOURS, now=None):
    """Extend one's own lease; refuses to touch anyone else's claim."""
    if not isinstance(agent, str) or not agent.strip():
        raise ValueError("agent is required")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task is required")
    if not isinstance(ttl_hours, (int, float)) or ttl_hours <= 0:
        raise ValueError("ttl_hours must be positive")
    owner = Path(coord_root) / "claims" / task / "owner.json"
    if not owner.is_file():
        raise ValueError("claim does not exist")
    payload = json.loads(owner.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("agent") != agent.strip():
        raise ValueError("only the owner may renew this claim")
    moment = _now(now)
    payload["expires_at"] = (moment + timedelta(hours=ttl_hours)).isoformat()
    payload["renewed_at"] = moment.isoformat()
    temporary = owner.with_suffix(".json.partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(owner)
    return {"schema": "agent-evolution-claim-renew-v1", "task": task, "agent": agent,
            "expires_at": payload["expires_at"], "renewed_at": payload["renewed_at"]}


def remediation(coord_root, *, now=None, stale_after_hours=DEFAULT_STALE_HOURS,
                ttl_hours=DEFAULT_CLAIM_TTL_HOURS):
    """Turn a conflict report into per-task actions an owner can actually run.

    A directory-wide claim held by an idle task blocks every sibling task that
    touches one file inside it. The report alone does not fix that, so each
    conflicting task gets one recommended action, ordered by severity.
    """
    report = analyze(coord_root, now=now, stale_after_hours=stale_after_hours, ttl_hours=ttl_hours)
    phases = {}
    states = Path(coord_root) / "task_states"
    if states.is_dir():
        for path in sorted(states.glob("*.json")):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(row, dict):
                phases[str(row.get("task") or path.stem)] = str(row.get("phase", ""))
    idle = {"needs-review", "done", "abandoned", "blocked"}
    actions, seen = [], set()

    def add(task, role, action, path, reason):
        key = (task, action)
        if key in seen:
            return
        seen.add(key)
        actions.append({"task": task, "role": role, "phase": phases.get(task, "unknown"),
                        "path": path, "action": action, "reason": reason})

    for conflict in report["conflicts"]:
        if conflict["kind"] == "expired_claim":
            add(conflict["tasks"][0], "owner", "release_or_renew_expired_claim", "",
                conflict["detail"])
            continue
        if conflict["severity"] != "blocking" or conflict["kind"] != "path_overlap":
            continue
        resolution = conflict.get("resolution") or {}
        winner, loser = resolution.get("winner"), resolution.get("loser")
        if not winner or not loser:
            continue
        winner_scopes = resolution.get("winner_scopes") or []
        loser_scopes = resolution.get("loser_scopes") or []
        coarse = any(low.lower().startswith(high.lower() + "/")
                     for high in winner_scopes for low in loser_scopes)
        winner_phase = phases.get(winner, "unknown")
        if winner_phase in idle:
            add(winner, "winner", "release_or_narrow_claim", conflict["path"],
                f"phase={winner_phase}; the claim outlived its work")
        elif coarse:
            add(winner, "winner", "narrow_coarse_scope", conflict["path"],
                "a directory-wide claim swallows sibling task scopes; narrow it to the files this task actually writes")
        phase = phases.get(loser, "unknown")
        add(loser, "loser",
            "release_or_narrow_claim" if phase in idle else "narrow_claim_before_writing",
            conflict["path"], f"phase={phase}")
    actions.sort(key=lambda row: (row["action"], row["task"], row["path"]))
    return {"schema": "agent-evolution-claim-remediation-v1",
            "checked_at": report["checked_at"],
            "status": report["status"],
            "blocking": report["blocking"],
            "actions": actions,
            "mutated_claims": False,
            "note": "Advisory only: only the claim owner (or the task leader) may release or narrow."}


def main(argv=None):
    """Read-only by default; ``claim`` and ``renew`` are the only writers."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="claim_conflicts", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    analyze_cmd = sub.add_parser("analyze", help="report every claim conflict")
    analyze_cmd.add_argument("coord_root")
    analyze_cmd.add_argument("--stale-after-hours", type=float, default=DEFAULT_STALE_HOURS)
    analyze_cmd.add_argument("--ttl-hours", type=float, default=DEFAULT_CLAIM_TTL_HOURS)
    gate_cmd = sub.add_parser("gate", help="check one intended write scope")
    gate_cmd.add_argument("coord_root")
    gate_cmd.add_argument("--agent", required=True)
    gate_cmd.add_argument("--task", required=True)
    gate_cmd.add_argument("--ttl-hours", type=float, default=DEFAULT_CLAIM_TTL_HOURS)
    gate_cmd.add_argument("paths", nargs="+")
    fix_cmd = sub.add_parser("remediation", help="per-task actions for blocking overlaps")
    fix_cmd.add_argument("coord_root")
    fix_cmd.add_argument("--stale-after-hours", type=float, default=DEFAULT_STALE_HOURS)
    fix_cmd.add_argument("--ttl-hours", type=float, default=DEFAULT_CLAIM_TTL_HOURS)
    claim_cmd = sub.add_parser("claim", help="gate, then create a claim with a lease deadline")
    claim_cmd.add_argument("coord_root")
    claim_cmd.add_argument("--agent", required=True)
    claim_cmd.add_argument("--task", required=True)
    claim_cmd.add_argument("--ttl-hours", type=float, default=DEFAULT_CLAIM_TTL_HOURS)
    claim_cmd.add_argument("paths", nargs="+")
    renew_cmd = sub.add_parser("renew", help="extend your own lease")
    renew_cmd.add_argument("coord_root")
    renew_cmd.add_argument("--agent", required=True)
    renew_cmd.add_argument("--task", required=True)
    renew_cmd.add_argument("--ttl-hours", type=float, default=DEFAULT_CLAIM_TTL_HOURS)
    args = parser.parse_args(argv)

    try:
        if args.command == "analyze":
            payload = analyze(args.coord_root, stale_after_hours=args.stale_after_hours,
                              ttl_hours=args.ttl_hours)
        elif args.command == "remediation":
            payload = remediation(args.coord_root, stale_after_hours=args.stale_after_hours,
                                  ttl_hours=args.ttl_hours)
        elif args.command == "claim":
            payload = claim(args.coord_root, agent=args.agent, task=args.task,
                            paths=args.paths, ttl_hours=args.ttl_hours)
        elif args.command == "renew":
            payload = renew(args.coord_root, agent=args.agent, task=args.task,
                            ttl_hours=args.ttl_hours)
        else:
            payload = gate(args.coord_root, agent=args.agent, task=args.task,
                           paths=args.paths, ttl_hours=args.ttl_hours)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.command == "gate" and not payload["allowed"]:
        return 3
    if args.command == "claim" and not payload["claimed"]:
        return 3
    if args.command in {"analyze", "remediation"} and payload["blocking"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
