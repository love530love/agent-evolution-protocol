"""One-shot, model-free bridge from persisted missions to the commander queue.

The project already owns a deterministic unattended loop: ``commander.tick``
(orders), ``engine.tick`` (federation DAG + a 900s heartbeat timeout),
``commander.watch`` / ``supervisor.serve`` (30s wrappers), and OS-level
launchers.  What was missing is that ``scripts/start-mission.ps1`` writes
``missions/M-*.json`` and *nothing ever consumes the resulting mission_id*:
there is no ``mission`` CLI command, and ``mission_supervisor.inspect`` only
returns action suggestions.

This module closes exactly that seam.  It is NOT a second scheduler:

  * it never sleeps, polls, spawns a process or starts a thread;
  * a tick is one external invocation of ``main()`` and then the process exits;
  * the only write it can perform is ``commander.submit_order``, which *queues*
    a DEPOSITED order and explicitly does not touch a desktop;
  * decisions come from ``mission_supervisor.inspect`` plus re-computable gates;
  * no model is imported, loaded or required.  A local model can neither decide
    whether a lease expired nor safely hold a fail-closed gate;
  * no new schema and no new ``coord-*`` command: the command surface stays
    generated from ``cli.build_parser``.

Scheduling belongs outside this repository.  ``scripts/run-mission-pulse.ps1``
is a bounded one-shot caller; ``scripts/register-mission-pulse-task.ps1`` is an
opt-in helper the operator may run explicitly.  Neither is executed by this
module, and registering an OS task is never a side effect of running a pulse.

Dry-run is the default.  ``--apply`` re-verifies every candidate from the files
before queueing, using a stable event id so a replay cannot duplicate an order.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from agent_evolution_protocol.isotime import parse_iso_or_none, to_utc
from . import commander
from . import mission_supervisor

NOTE = ("One-shot bridge only: read missions, gate, optionally queue a DEPOSITED "
        "order. No model, no daemon, no timer, no desktop, no system task.")

DEFAULT_SENDER = "missionpulse"
SAFE_AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ASSIGNABLE_ACTIONS = frozenset({"assign"})
WAITING_PHASES = frozenset({"waiting", "waiting_user", "waiting_external"})
TERMINAL_PHASES = frozenset({"done", "abandoned", "succeeded", "cancelled", "completed"})


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def registered_members(root: Path) -> set[str]:
    """Names actually registered in this coordination root.

    ``valid_agent`` only checks characters, so an unregistered target would
    silently create a mailbox nobody reads.  Membership is derived from files,
    never from a hardcoded roster that drifts away from the workspace.
    """
    names = set()
    for folder in ("presence/agents", "clients"):
        directory = Path(root) / folder
        if directory.is_dir():
            names.update(p.stem.lower() for p in directory.glob("*.json"))
    return {name for name in names if SAFE_AGENT.fullmatch(name)}


def existing_event_ids(root: Path) -> set[str]:
    folder = Path(root) / "commander" / "inbox"
    if not folder.is_dir():
        return set()
    out = set()
    for path in folder.glob("*.json"):
        event = str(_read(path).get("event_id") or "")
        if event:
            out.add(event)
    return out


def event_id_for(mission: dict, stage: dict) -> str:
    """Stable per (mission, stage, stage state, stage revision, goal)."""
    material = "|".join(str(value) for value in (
        mission.get("mission_id", ""), stage.get("stage_id", ""),
        stage.get("state", ""), stage.get("updated_at", ""), mission.get("goal", ""),
    ))
    return "PULSE-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20].upper()


def build_text(mission: dict, stage: dict) -> str:
    return "\n".join([
        "[AEP-MISSION-PULSE] 长任务阶段派工（只入队，不代表已送达）",
        f"mission_id: {mission.get('mission_id', '')}",
        f"stage_id: {stage.get('stage_id', '')}",
        f"goal: {mission.get('goal', '')}",
        f"stage: {stage.get('description', '')}",
        "完成后用 coord-task-state 写回 phase 与 evidence；只有 VERIFIED 才能关单。",
        "不要新建会话、不要消费旧 wake、不要自动重试高风险动作。",
    ])


def _task_name(mission: dict) -> str:
    return str(mission.get("task") or mission.get("mission_id") or "")


def _moment(value, *, assume_utc: bool = True):
    return parse_iso_or_none(str(value or ""), assume_utc=assume_utc)


def gates(*, root: Path, mission: dict, stage: dict, action: str,
          members: set[str], queued_events: set[str], event_id: str, now) -> list[str]:
    """All reasons this stage must NOT be queued. Empty means allowed."""
    reasons: list[str] = []
    if action not in ASSIGNABLE_ACTIONS:
        reasons.append("ACTION_NOT_ASSIGNABLE")

    target = str(stage.get("actor") or "")
    if not target:
        reasons.append("NO_TARGET")
    elif not SAFE_AGENT.fullmatch(target):
        reasons.append("INVALID_AGENT_NAME")
    elif target not in members:
        reasons.append("UNKNOWN_AGENT")

    deadline = _moment(mission.get("deadline"))
    if deadline is not None and to_utc(deadline) < to_utc(now):
        reasons.append("MISSION_PAST_DEADLINE")

    task = _task_name(mission)
    state = _read(Path(root) / "task_states" / f"{task}.json")
    if state:
        if state.get("human_required"):
            reasons.append("HUMAN_REQUIRED")
        phase = str(state.get("phase", "")).lower()
        if phase in WAITING_PHASES:
            reasons.append("WAITING_FOR_EVENT")
        if phase in TERMINAL_PHASES:
            reasons.append("TASK_TERMINAL")

    # An existing claim is a fence, not an invitation to take over.  The kernel
    # hardcodes automatic_takeover_allowed=False; this mirror stays fail-closed.
    claim = _read(Path(root) / "claims" / task / "owner.json")
    if claim:
        owner = str(claim.get("agent") or "")
        expiry = _moment(claim.get("expires_at"))
        if owner and owner != target:
            reasons.append("CLAIM_OWNER_MISMATCH")
        if expiry is not None and to_utc(expiry) <= to_utc(now):
            reasons.append("CLAIM_EXPIRED")

    if event_id in queued_events:
        reasons.append("ALREADY_QUEUED")
    return reasons


def _reject(mission_id: str, stage_id: str, reasons: list[str]) -> dict:
    return {"mission_id": mission_id, "stage_id": stage_id,
            "reason": reasons[0] if reasons else "", "reasons": list(reasons)}


def scan(root, *, member_names=None, now=None) -> dict:
    """Read-only decision pass. Never writes a file."""
    from datetime import datetime, timezone

    root = Path(root)
    now = now or datetime.now(timezone.utc)
    members = set(member_names) if member_names is not None else registered_members(root)
    queued_events = existing_event_ids(root)
    actions: dict[str, int] = {}
    candidates: list[dict] = []
    rejected: list[dict] = []
    mission_paths = sorted((root / "missions").glob("M-*.json")) if (root / "missions").is_dir() else []

    decisions = {row["mission_id"]: row for row in mission_supervisor.inspect(root)["actions"]}
    for path in mission_paths:
        try:
            mission = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(mission, dict):
                raise ValueError("mission must be an object")
        except (OSError, ValueError):
            rejected.append(_reject(path.stem, "", ["MISSION_UNREADABLE"]))
            continue
        mission_id = str(mission.get("mission_id") or path.stem)
        decision = decisions.get(mission_id, {})
        action = str(decision.get("action") or "")
        actions[action or "unknown"] = actions.get(action or "unknown", 0) + 1
        stage_ids = list(decision.get("stages") or [])
        # report_delivery / blocked_review carry no stage ids; still surface the
        # decision so "nothing happened" is distinguishable from "no missions".
        stages = [s for s in mission.get("stages", []) if s.get("stage_id") in stage_ids]
        if not stages:
            stages = [{"stage_id": "", "state": "", "description": ""}]
        for stage in stages:
            stage_id = str(stage.get("stage_id") or "")
            event_id = event_id_for(mission, stage)
            reasons = gates(root=root, mission=mission, stage=stage, action=action,
                            members=members, queued_events=queued_events,
                            event_id=event_id, now=now)
            if reasons:
                rejected.append(_reject(mission_id, stage_id, reasons))
                continue
            candidates.append({
                "mission_id": mission_id, "stage_id": stage_id, "action": action,
                "target": str(stage.get("actor") or ""), "task": _task_name(mission),
                "workspace_id": str(mission.get("workspace_id") or ""),
                "event_id": event_id, "reasons": [],
                "mission_path": str(path),
                "mission_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "text": build_text(mission, stage),
            })
    return {"root": str(root), "scanned": len(mission_paths), "actions": actions,
            "candidates": candidates, "rejected": rejected,
            "member_names": sorted(members), "queued_event_ids": len(queued_events),
            "note": NOTE}


def verify(candidate: dict, *, member_names=None, now=None) -> dict:
    """Recompute every gate from the files before any write is allowed."""
    from datetime import datetime, timezone

    now = now or datetime.now(timezone.utc)
    path = Path(candidate["mission_path"])
    try:
        raw = path.read_bytes()
        mission = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return {"verified": False, "reason": "mission-unreadable"}
    if hashlib.sha256(raw).hexdigest() != candidate["mission_sha256"]:
        return {"verified": False, "reason": "mission-changed"}
    stage = next((s for s in mission.get("stages", [])
                  if s.get("stage_id") == candidate["stage_id"]), None)
    if stage is None or event_id_for(mission, stage) != candidate["event_id"]:
        return {"verified": False, "reason": "event-id-mismatch"}
    root = path.parent.parent
    members = set(member_names) if member_names is not None else registered_members(root)
    reasons = gates(root=root, mission=mission, stage=stage,
                    action=candidate["action"], members=members,
                    queued_events=existing_event_ids(root),
                    event_id=candidate["event_id"], now=now)
    if reasons:
        return {"verified": False, "reason": "gate:" + reasons[0]}
    if build_text(mission, stage) != candidate["text"]:
        return {"verified": False, "reason": "text-changed"}
    return {"verified": True, "reason": ""}


def queue_candidate(root, candidate: dict, *, sender: str = DEFAULT_SENDER,
                    member_names=None, now=None) -> dict:
    """Verify-then-queue for one candidate. This is the only write path.

    A rejected candidate is dead *evidence*, not a permanently banned file: the
    next external invocation re-scans and may produce a fresh candidate. What
    must never happen is queueing on the strength of stale evidence.
    """
    check = verify(candidate, member_names=member_names, now=now)
    if not check["verified"]:
        return {"queued": None, "reason": check["reason"]}
    # Re-check the idempotency key immediately before the only write.
    if candidate["event_id"] in existing_event_ids(Path(root)):
        return {"queued": None, "reason": "ALREADY_QUEUED"}
    order = commander.submit_order(
        Path(root), target=candidate["target"], task=candidate["task"],
        text=candidate["text"], sender=sender,
        event_id=candidate["event_id"],
        workspace_id=candidate["workspace_id"])
    return {"queued": {
        "mission_id": candidate["mission_id"], "stage_id": candidate["stage_id"],
        "event_id": candidate["event_id"], "order_id": order["id"],
        "target": order["target"], "state": order["state"],
        "workspace_id": candidate["workspace_id"],
    }, "reason": ""}


def pulse(root, *, apply: bool = False, sender: str = DEFAULT_SENDER,
          member_names=None, now=None) -> dict:
    """One bounded pass. ``apply=True`` is the only path that can write."""
    report = scan(root, member_names=member_names, now=now)
    rejected = [{"mission_id": row["mission_id"], "stage_id": row["stage_id"],
                 "reason": reason}
                for row in report["rejected"] for reason in row["reasons"]]
    verified, queued = [], []
    for candidate in report["candidates"]:
        check = verify(candidate, member_names=member_names, now=now)
        if not check["verified"]:
            rejected.append({"mission_id": candidate["mission_id"],
                             "stage_id": candidate["stage_id"],
                             "reason": check["reason"]})
            continue
        verified.append({"mission_id": candidate["mission_id"],
                         "stage_id": candidate["stage_id"],
                         "event_id": candidate["event_id"]})
        if not apply:
            continue
        result = queue_candidate(root, candidate, sender=sender,
                                 member_names=member_names, now=now)
        if result["queued"] is None:
            rejected.append({"mission_id": candidate["mission_id"],
                             "stage_id": candidate["stage_id"],
                             "reason": result["reason"]})
        else:
            queued.append(result["queued"])
    return {
        "root": report["root"], "scanned": report["scanned"],
        "actions": report["actions"], "candidates": len(report["candidates"]),
        "verified": verified, "rejected": rejected, "queued": queued,
        "applied": bool(apply),
        "models_loaded": 0, "processes_started": 0, "ui_touched": False,
        "deployment_performed": False, "system_tasks_registered": False,
        "note": NOTE,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coord-root", type=Path,
                        default=Path.cwd() / "coordination",
                        help="coordination runtime root (default: ./coordination)")
    parser.add_argument("--apply", action="store_true",
                        help="queue verified candidates as DEPOSITED orders")
    parser.add_argument("--sender", default=DEFAULT_SENDER)
    args = parser.parse_args(argv)
    report = pulse(args.coord_root, apply=args.apply, sender=args.sender)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
