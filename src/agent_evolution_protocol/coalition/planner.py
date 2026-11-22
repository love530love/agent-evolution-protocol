"""Deterministic primary/backup planning for quota- and time-limited fleets.

The planner never dispatches.  It merges the mission spec, presence evidence,
the capacity ledger and fresh capability declarations into one recomputable
report: who holds each role now, who could take over, what blocks a takeover
and which single action to take next.  Stale presence is a review item, not
proof the holder is dead; missing capacity evidence is ``UNKNOWN``, not healthy.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..coordination import presence as _presence
from . import capacity, io, spec as _spec

SCHEMA = "aep-coalition-plan-v1"
ACTION_ORDER = {
    "TAKEOVER_NOW": 0,
    "ASSIGN_PRIMARY": 1,
    "VERIFY_THEN_TAKEOVER": 2,
    "VERIFY_HOLDER": 3,
    "PREPARE_HANDOFF": 4,
    "REPLAN": 5,
    "HOLD": 6,
    "BLOCKED": 7,
}
LEVELS = {"full": 3, "acting": 2, "scribe": 1, "none": 0}
AUTHORITY = {"full": "primary", "acting": "acting", "scribe": "scribe",
             "none": "none"}


def _presence_rows(coord_root, at):
    rows = {}
    folder = Path(coord_root) / "presence" / "agents"
    for path in sorted(folder.glob("*.json")):
        try:
            row = io.read_json(path)
        except (OSError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        agent = row.get("agent")
        if not isinstance(agent, str) or not _presence.NAME.fullmatch(agent):
            continue
        try:
            state = _presence.effective(row, at)
        except (TypeError, ValueError):
            state = "unknown"
        rows[agent] = {
            "state": state,
            "source": row.get("source"),
            "observed_at": row.get("observed_at"),
            "expires_at": row.get("expires_at"),
            "session_id": row.get("session_id"),
        }
    return rows


def _roster_capabilities(coord_root, at):
    merged, sources = {}, {}
    for path in sorted((Path(coord_root) / "capabilities").glob("*.json")):
        try:
            row = io.read_json(path)
        except (OSError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        agent = row.get("agent")
        if not isinstance(agent, str) or not _presence.NAME.fullmatch(agent):
            continue
        expiry = io.parse_utc_or_none(row.get("available_until"))
        if expiry is None or expiry <= at:
            continue
        tokens = set()
        for key in ("chips", "compilers", "strengths"):
            for item in row.get(key) or []:
                if isinstance(item, str) and _spec.SAFE_TOKEN.fullmatch(item):
                    tokens.add(item)
        merged.setdefault(agent, set()).update(tokens)
        sources.setdefault(agent, []).append(str(path))
    return ({agent: sorted(tokens) for agent, tokens in merged.items()},
            {agent: sorted(paths) for agent, paths in sources.items()})


def _handoff_ledger(coord_root):
    path = Path(coord_root) / "command_handoff" / "state.json"
    try:
        row = io.read_json(path)
    except (OSError, ValueError):
        return {"present": True, "readable": False, "roles": {},
                "note": "command_handoff state is corrupt; ignored, not repaired."}
    if row is None:
        return {"present": False, "readable": True, "roles": {},
                "note": "No command_handoff ledger; the coalition plan stays self-contained."}
    roles = {}
    if isinstance(row, dict) and isinstance(row.get("roles"), dict):
        for role, record in sorted(row["roles"].items()):
            if isinstance(record, dict):
                roles[role] = {"state": record.get("state"),
                               "holder": record.get("holder")}
    return {"present": True, "readable": True, "roles": roles,
            "note": "Advisory only; the coalition planner does not mutate this ledger."}


def _agent_views(coord_root, mission, at):
    presence_rows = _presence_rows(coord_root, at)
    roster_caps, roster_sources = _roster_capabilities(coord_root, at)
    mission_agents = {item["agent"]: item for item in mission["agents"]}
    names = set(mission_agents) | set(mission["current_holders"].values())
    views = {}
    for agent in sorted(names):
        declared = mission_agents.get(agent)
        tokens = set(declared["capabilities"]) if declared else set()
        tokens.update(roster_caps.get(agent, []))
        sources = []
        if declared and declared["capabilities"]:
            sources.append("mission")
        if roster_sources.get(agent):
            sources.append("roster")
        views[agent] = {
            "agent": agent,
            "vendor": declared["vendor"] if declared else None,
            "declared": declared is not None,
            "capabilities": sorted(tokens),
            "capability_sources": sources,
            "max_primary_roles": (declared["max_primary_roles"] if declared else
                                  _spec.DEFAULT_POLICY["max_primary_roles_per_agent"]),
            "subagent_slots": declared["subagent_slots"] if declared else 0,
            "presence": presence_rows.get(agent, {
                "state": "unknown", "source": None, "observed_at": None,
                "expires_at": None, "session_id": None}),
            "capacity": capacity.read(coord_root, agent, now=at),
        }
    return views


def _capability_level(role, skills):
    if set(role["required_capabilities"]) <= set(skills):
        return "full"
    if set(role["acting_capabilities"]) <= set(skills):
        return "acting"
    return "scribe" if role["allow_scribe_fallback"] else "none"


def _candidate(role, view):
    level = _capability_level(role, view["capabilities"])
    if level == "none":
        return None
    presence_state = view["presence"]["state"]
    cap = view["capacity"]
    cap_ok = bool(cap["fresh"]) and cap["state"] in capacity.RUNNABLE_STATES
    ready = presence_state == "online" and cap_ok
    blockers = []
    if presence_state == "offline":
        blockers.append("PRESENCE_OFFLINE")
    elif presence_state in ("stale", "unknown"):
        blockers.append("PRESENCE_UNVERIFIED")
    if cap["state"] == "EXHAUSTED":
        blockers.append("CAPACITY_EXHAUSTED")
    elif not cap["fresh"]:
        blockers.append("CAPACITY_UNKNOWN")
    elif cap["state"] == "LOW":
        blockers.append("CAPACITY_LOW")
    return {
        "agent": view["agent"],
        "vendor": view["vendor"],
        "declared": view["declared"],
        "level": level,
        "authority": AUTHORITY[level],
        "capabilities": list(view["capabilities"]),
        "presence": dict(view["presence"]),
        "capacity": {key: cap[key] for key in (
            "state", "fresh", "reason", "source", "observed_at", "expires_at",
            "remaining_fraction", "remaining_seconds", "resets_at")},
        "ready": ready,
        "blockers": blockers,
        "connection_slots": max(1, view["subagent_slots"]),
    }


def _rank(candidate, role):
    level = LEVELS[candidate["level"]]
    preferred = 1 if role["preferred_vendor"] and \
        candidate["vendor"] == role["preferred_vendor"] else 0
    cap_rank = {"AVAILABLE": 2, "LOW": 1}.get(candidate["capacity"]["state"], 0)
    presence_rank = {"online": 2, "stale": 1}.get(candidate["presence"]["state"], 0)
    return (-int(candidate["ready"]), -level, -preferred, -cap_rank,
            -presence_rank, candidate["agent"])


def _summarize(candidate):
    if candidate is None:
        return None
    return {
        "agent": candidate["agent"],
        "vendor": candidate["vendor"],
        "level": candidate["level"],
        "authority": candidate["authority"],
        "ready": candidate["ready"],
        "presence": candidate["presence"]["state"],
        "capacity": candidate["capacity"]["state"],
        "overloaded": bool(candidate.get("agent_role_overflow")),
        "blockers": list(candidate["blockers"]),
    }


def _handoff_key(mission, role, from_agent, to_agent, trigger):
    return "HO-" + io.digest({
        "mission": mission,
        "role": role,
        "from": from_agent,
        "to": to_agent,
        "trigger": trigger,
    })[:16]


def _command(coord_root, mission, role, from_agent, to_agent, trigger, authority):
    head = ("aep coord-coalition takeover"
            f" --coord-root \"{coord_root}\" --mission \"{mission}\""
            f" --role {role} --from {from_agent or '-'} --to {to_agent}"
            f" --trigger {trigger} --authority {authority}")
    dry = head + " --dry-run"
    execute = (head + " --execute --sender <commander> "
                      "--evidence <durable-evidence-path>")
    return dry, execute


def _select(role, candidates, load, policy):
    """Return (chosen, skipped): a ready candidate, else an overloaded one, else review."""
    ranked = sorted(candidates, key=lambda item: _rank(item, role))
    ready = [item for item in ranked if item["ready"]]
    skipped = []
    overloaded = None
    for candidate in ready:
        vendor = candidate["vendor"] or "unknown"
        over_vendor = load["vendors"].get(vendor, 0) >= policy["max_roles_per_vendor"]
        over_critical = (role["criticality"] == "critical" and
                         load["critical_vendors"].get(vendor, 0) >=
                         policy["max_critical_roles_per_vendor"])
        if over_vendor:
            skipped.append({"agent": candidate["agent"], "reason": "VENDOR_CAP"})
        if over_critical:
            skipped.append({"agent": candidate["agent"], "reason": "CRITICAL_VENDOR_CAP"})
        if (over_vendor or over_critical) and not policy["allow_vendor_overflow"]:
            continue
        if (load["agents"].get(candidate["agent"], 0) >=
                load["views"][candidate["agent"]]["max_primary_roles"]):
            skipped.append({"agent": candidate["agent"], "reason": "AGENT_ROLE_CAP"})
            if overloaded is None:
                overloaded = candidate
            continue
        return candidate, skipped
    if overloaded is not None:
        overloaded["agent_role_overflow"] = True
        return overloaded, skipped
    review = [item for item in ranked if not item["ready"]]
    return (review[0] if review else None), skipped


def _commit(load, candidate, role):
    if candidate is None or not candidate["ready"]:
        return
    load["agents"][candidate["agent"]] = load["agents"].get(candidate["agent"], 0) + 1
    vendor = candidate["vendor"] or "unknown"
    load["vendors"][vendor] = load["vendors"].get(vendor, 0) + 1
    if role["criticality"] == "critical":
        load["critical_vendors"][vendor] = load["critical_vendors"].get(vendor, 0) + 1


def _backups(role, candidates, primary, depth):
    pool = [item for item in candidates
            if (primary is None or item["agent"] != primary["agent"])
            and "CAPACITY_EXHAUSTED" not in item["blockers"]
            and "PRESENCE_OFFLINE" not in item["blockers"]]
    pool.sort(key=lambda item: (
        -int(item["ready"]), -LEVELS[item["level"]],
        0 if (role["criticality"] == "critical" and primary is not None
              and item["vendor"] != primary["vendor"]) else 1,
        item["agent"]))
    return pool[:max(0, depth)]


def _risk(risks, code, severity, role, message, agents=()):
    risks.append({"code": code, "severity": severity, "role": role,
                  "agents": sorted(agents), "message": message})


def _role_plan(role, holder, candidates, holder_candidate, load, policy,
               coord_root, mission_id):
    risks = []
    primary = None
    proposed = None
    coverage = "BLOCKED"
    action = "BLOCKED"
    reason = "NO_ELIGIBLE_CANDIDATE"
    target = None
    trigger = "none"
    authority = "none"

    holder_ready = bool(holder_candidate and holder_candidate["ready"])
    holder_failed = bool(holder_candidate and (
        "CAPACITY_EXHAUSTED" in holder_candidate["blockers"] or
        "PRESENCE_OFFLINE" in holder_candidate["blockers"]))

    if holder is not None and holder_candidate is not None and holder_ready:
        primary = holder_candidate
        coverage = "DEGRADED" if primary["level"] == "scribe" else "COVERED"
        if primary["capacity"]["state"] == "LOW":
            action, reason = "PREPARE_HANDOFF", "HOLDER_CAPACITY_LOW"
            trigger = "credit_warning"
        else:
            action, reason = "HOLD", "HOLDER_READY"
    elif holder is not None and holder_candidate is not None and holder_failed:
        successor, _ = _select(role, [item for item in candidates
                                      if item["agent"] != holder], load, policy)
        proposed = successor
        trigger = ("offline" if "PRESENCE_OFFLINE" in holder_candidate["blockers"]
                   else "quota_exhausted")
        if successor and successor["ready"]:
            primary = successor
            coverage = "DEGRADED" if successor["level"] == "scribe" else "COVERED"
            target, authority = successor["agent"], successor["authority"]
            action, reason = "TAKEOVER_NOW", "HOLDER_" + trigger.upper()
        elif successor:
            target, authority = successor["agent"], successor["authority"]
            coverage, action, reason = "UNVERIFIED", "VERIFY_THEN_TAKEOVER", \
                "SUCCESSOR_UNVERIFIED"
        else:
            coverage, action, reason = "BLOCKED", "BLOCKED", \
                "HOLDER_FAILED_NO_SUCCESSOR"
    elif holder is not None and holder_candidate is not None:
        primary = holder_candidate
        coverage, action, reason = "UNVERIFIED", "VERIFY_HOLDER", \
            "HOLDER_EVIDENCE_UNVERIFIED"
        trigger = "holder_unverified"
    elif holder is not None and holder_candidate is None:
        successor, _ = _select(role, candidates, load, policy)
        proposed = successor
        if successor and successor["ready"]:
            primary = successor
            coverage = "DEGRADED" if successor["level"] == "scribe" else "COVERED"
            target, trigger, authority = successor["agent"], "capability_mismatch", \
                successor["authority"]
            action, reason = "TAKEOVER_NOW", "HOLDER_INCAPABLE"
        elif successor:
            target, trigger, authority = successor["agent"], "capability_mismatch", \
                successor["authority"]
            coverage, action, reason = "UNVERIFIED", "VERIFY_THEN_TAKEOVER", \
                "SUCCESSOR_UNVERIFIED"
        else:
            coverage, action, reason = "BLOCKED", "BLOCKED", \
                "HOLDER_INCAPABLE_NO_SUCCESSOR"
    else:
        candidate, _ = _select(role, candidates, load, policy)
        if candidate and candidate["ready"]:
            primary = candidate
            coverage = "DEGRADED" if candidate["level"] == "scribe" else "COVERED"
            target, trigger, authority = candidate["agent"], "bootstrap", \
                candidate["authority"]
            action, reason = "ASSIGN_PRIMARY", "NO_HOLDER_BOOTSTRAP"
        elif candidate:
            proposed = candidate
            coverage, action, reason = "UNVERIFIED", "VERIFY_THEN_TAKEOVER", \
                "CANDIDATE_UNVERIFIED"
            target, trigger, authority = candidate["agent"], "bootstrap", \
                candidate["authority"]
        else:
            coverage, action, reason = "BLOCKED", "BLOCKED", "NO_ELIGIBLE_CANDIDATE"

    _commit(load, primary, role)
    backups = _backups(role, candidates, primary, policy["backup_depth"])
    if action == "PREPARE_HANDOFF" and target is None and backups:
        target = backups[0]["agent"]
        authority = backups[0]["authority"]
    if action == "VERIFY_HOLDER" and target is None and backups:
        target = backups[0]["agent"]
        authority = backups[0]["authority"]
    role_authority = primary["authority"] if primary is not None else "none"
    if action == "HOLD":
        authority = role_authority

    if primary is not None and primary.get("agent_role_overflow"):
        coverage = "DEGRADED"
        _risk(risks, "AGENT_OVERLOADED", "high", role["id"],
              "No agent under its primary-role cap can cover this role; it was "
              "assigned to an already-loaded agent to avoid a stall.",
              [primary["agent"]])
    if coverage == "DEGRADED" and primary and primary["level"] == "scribe":
        _risk(risks, "SCRIBE_FALLBACK",
              "high" if role["criticality"] == "critical" else "medium",
              role["id"],
              "Role is covered only by a record-and-relay scribe; no irreversible "
              "external action may be delegated.", [primary["agent"]])
    ready_backups = [item for item in backups
                     if item["ready"] and item["level"] in ("full", "acting")]
    if role["criticality"] == "critical" and policy["require_backup"]:
        if not backups:
            _risk(risks, "NO_BACKUP", "high", role["id"],
                  "Critical role has no declared backup; a quota or offline event "
                  "stalls it.")
        elif not ready_backups:
            _risk(risks, "NO_READY_BACKUP", "high", role["id"],
                  "Declared backups exist but none is currently verified; a quota "
                  "or offline event would stall the role.")
    if (role["criticality"] == "critical" and primary is not None and ready_backups
            and all(item["vendor"] == primary["vendor"] for item in ready_backups)):
        _risk(risks, "BACKUP_SAME_VENDOR", "medium", role["id"],
              "Every ready backup shares the primary's vendor; one provider outage "
              "removes both.", [primary["agent"]] + [item["agent"] for item in ready_backups])

    draft = None
    if target:
        dry, execute = _command(coord_root, mission_id, role["id"], holder,
                                target, trigger, authority)
        draft = {
            "schema": "aep-coalition-handoff-draft-v1",
            "key": _handoff_key(mission_id, role["id"], holder, target, trigger),
            "mission": mission_id,
            "role": role["id"],
            "from": holder,
            "to": target,
            "trigger": trigger,
            "authority": authority,
            "instruction_outline": (
                f"接手 {role['id']}（{role['title']}）；权限 {authority}；"
                f"写入范围：{', '.join(role['write_scope']) or '无（只读/记录）'}；"
                "保持原会话和模型；先回复 ACK，再按角色约束执行；"
                "不得把收到消息当作已接单，不得盲目重放结果不明的写操作。"),
            "dry_run_command": dry,
            "execute_command": execute,
            "execute_allowed": action in ("TAKEOVER_NOW", "ASSIGN_PRIMARY"),
            "delivery_claim": "NONE",
        }

    tick = {
        "role": role["id"],
        "action": action,
        "reason": reason,
        "from": holder,
        "target": target,
        "trigger": trigger,
        "authority": authority,
        "handoff_key": draft["key"] if draft else None,
        "execute_allowed": bool(draft and draft["execute_allowed"]),
        "requires_verification": action in ("VERIFY_HOLDER", "VERIFY_THEN_TAKEOVER"),
    }
    return {
        "id": role["id"],
        "title": role["title"],
        "criticality": role["criticality"],
        "load": role["load"],
        "required_capabilities": role["required_capabilities"],
        "acting_capabilities": role["acting_capabilities"],
        "write_scope": role["write_scope"],
        "allow_scribe_fallback": role["allow_scribe_fallback"],
        "holder": _summarize(holder_candidate),
        "proposed": _summarize(proposed),
        "primary": _summarize(primary),
        "authority": role_authority,
        "coverage": coverage,
        "backups": [_summarize(item) for item in backups],
        "blockers": list(holder_candidate["blockers"]) if holder_candidate else [],
        "risks": risks,
        "tick": tick,
        "handoff_draft": draft,
    }


def _plan_verdict(roles, risks):
    if any(role["coverage"] == "BLOCKED" for role in roles.values()):
        return "BLOCKED_NO_CANDIDATE"
    if any(role["coverage"] == "UNVERIFIED" for role in roles.values()):
        return "BLOCKED_UNTIL_WAKE"
    if any(role["coverage"] == "DEGRADED" for role in roles.values()):
        return "DEGRADED"
    if any(risk["severity"] in ("critical", "high") for risk in risks):
        return "DEGRADED"
    return "READY"


def _merge_risk(risks, role_risks):
    for risk in role_risks:
        if risk not in risks:
            risks.append(risk)


def plan(coord_root, mission_source, *, now=None):
    """Build one deterministic coalition plan.

    ``mission_source`` is a validated mission spec path or object (see
    :func:`spec.load_spec`).  ``now`` is the single evaluation instant.
    """
    coord_root = Path(coord_root)
    mission = _spec.load_spec(mission_source)
    at = io.coerce_utc(now or datetime.now().astimezone(), "now")
    views = _agent_views(coord_root, mission, at)
    policy = mission["vendor_policy"]
    load = {"agents": {}, "vendors": {}, "critical_vendors": {}, "views": views}
    roles = {}
    risks = []
    ordered_roles = sorted(mission["roles"],
                           key=lambda item: (0 if item["criticality"] == "critical" else 1,
                                             item["id"]))
    drafts = {}
    for role in ordered_roles:
        candidates = [candidate for agent in sorted(views)
                      if (candidate := _candidate(role, views[agent])) is not None]
        holder = mission["current_holders"].get(role["id"])
        holder_candidate = _candidate(role, views[holder]) if holder is not None else None
        if holder is not None and not views[holder]["declared"]:
            _risk(risks, "UNDECLARED_HOLDER", "medium", role["id"],
                  "Current holder is not declared in the mission roster; only "
                  "roster capabilities and observed evidence apply.", [holder])
        if holder is not None and holder_candidate is None:
            _risk(risks, "HOLDER_INCAPABLE", "critical", role["id"],
                  "Declared holder lacks both required and acting capabilities.",
                  [holder])
        role_plan = _role_plan(role, holder, candidates, holder_candidate,
                               load, policy, coord_root, mission["mission"])
        if role_plan["handoff_draft"]:
            drafts[role_plan["handoff_draft"]["key"]] = role_plan["handoff_draft"]
        _merge_risk(risks, role_plan["risks"])
        roles[role["id"]] = role_plan

    active_sessions = mission.get("active_sessions") or {}
    for role_id, session in sorted(active_sessions.items()):
        agent = session["agent"]
        holder = mission["current_holders"].get(role_id)
        if agent not in views or not views[agent]["declared"]:
            _risk(risks, "UNDECLARED_ACTIVE_SESSION", "medium", role_id,
                  "A session declares itself active for this role but is absent "
                  "from the mission roster; the declaration is context only.",
                  [agent])
        if holder != agent:
            _risk(risks, "ACTIVE_SESSION_NOT_HOLDER", "medium", role_id,
                  f"Declared active session is {agent}, while the static holder "
                  f"is {holder or 'none'}; no authority is transferred by this "
                  "declaration.", [agent] + ([holder] if holder else []))
        if not session["acknowledged"]:
            _risk(risks, "ACTIVE_SESSION_ACK_UNVERIFIED", "medium", role_id,
                  "Declared active session has no original-session ACK; treat it "
                  "as context, not a handoff.", [agent])

    vendor_totals = {}
    for role_plan in roles.values():
        if role_plan["primary"]:
            vendor = role_plan["primary"]["vendor"] or "unknown"
            vendor_totals[vendor] = vendor_totals.get(vendor, 0) + 1
    for vendor, count in sorted(vendor_totals.items()):
        if count > policy["max_roles_per_vendor"]:
            _risk(risks, "VENDOR_OVERFLOW", "high", None,
                  f"Vendor {vendor} holds {count} primary roles; policy cap is "
                  f"{policy['max_roles_per_vendor']}.")
    if len(vendor_totals) == 1 and not policy["allow_single_vendor"] and len(roles) > 1:
        _risk(risks, "SINGLE_VENDOR", "critical", None,
              "Every primary role resolves to one vendor; one outage removes the "
              "whole command chain.", list(vendor_totals))
    for role_plan in roles.values():
        for backup in role_plan["backups"]:
            if not backup["ready"] and backup["presence"] in ("stale", "unknown"):
                _risk(risks, "BACKUP_UNVERIFIED", "medium", role_plan["id"],
                      "Backup presence or capacity is unverified; wake and confirm "
                      "before relying on it.", [backup["agent"]])
        primary = role_plan["primary"]
        if primary and primary["capacity"] == "UNKNOWN":
            _risk(risks, "CAPACITY_UNKNOWN", "medium", role_plan["id"],
                  "Primary has no fresh capacity evidence; availability is unproven.",
                  [primary["agent"]])
        if role_plan["coverage"] == "UNVERIFIED":
            _risk(risks, "VERIFICATION_REQUIRED", "high", role_plan["id"],
                  "Coverage depends on unverified presence or capacity evidence.",
                  [role_plan["tick"]["target"]] if role_plan["tick"]["target"] else [])
        if role_plan["tick"]["action"] == "BLOCKED":
            _risk(risks, "ROLE_BLOCKED", "critical", role_plan["id"],
                  f"Role is blocked: {role_plan['tick']['reason']}.")

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    risks.sort(key=lambda item: (severity_order.get(item["severity"], 9),
                                 item["role"] or "", item["code"]))

    actions = [role_plan["tick"] for role_plan in roles.values()
               if role_plan["tick"]["action"] != "HOLD"]
    actions.sort(key=lambda item: (ACTION_ORDER.get(item["action"], 99),
                                   item["role"]))
    verdict = _plan_verdict(roles, risks)
    body = {
        "schema": SCHEMA,
        "at": at.isoformat(),
        "coord_root": str(coord_root),
        "mission": mission["mission"],
        "description": mission["description"],
        "vendor_policy": policy,
        "agents": {agent: {"vendor": view["vendor"],
                           "declared": view["declared"],
                           "capabilities": view["capabilities"],
                           "presence": view["presence"]["state"],
                           "capacity": view["capacity"]["state"]}
                   for agent, view in sorted(views.items())},
        "roles": roles,
        "risks": risks,
        "tick": {"actions": actions,
                 "count": len(actions),
                 "execute_ready": sum(1 for item in actions if item["execute_allowed"])},
        "handoff_drafts": drafts,
        "handoff_ledger": _handoff_ledger(coord_root),
        "active_sessions": {
            role_id: dict(session, advisory_only=True,
                          authority_transfer=False)
            for role_id, session in sorted(active_sessions.items())
        },
        "active_sessions_note": (
            "Declared active sessions are context only. They never change the "
            "static holder, primary, authority or readiness; a handoff still "
            "requires an original-session ACK."
        ),
        "execution": {"delivery_claim": "NONE", "started": False,
                      "note": "Planning only. A dispatch is a separate guarded call; "
                              "a queued wake, mailbox deposit and ACK are three "
                              "different evidence levels."},
        "verdict": verdict,
        "note": "READY is a planning verdict, not delivery or ACK evidence.",
    }
    body["input_digest"] = io.digest({
        "mission": mission,
        "agents": body["agents"],
        "active_sessions": active_sessions,
        "roles": {role_id: {"holder": item["holder"], "primary": item["primary"],
                            "backups": item["backups"],
                            "coverage": item["coverage"]}
                  for role_id, item in roles.items()},
        "at": at.isoformat(),
    })
    body["plan_digest"] = io.digest(body)
    return body


def tick(coord_root, mission_source, *, now=None):
    report = plan(coord_root, mission_source, now=now)
    return {
        "schema": "aep-coalition-tick-v1",
        "at": report["at"],
        "mission": report["mission"],
        "plan_digest": report["plan_digest"],
        "verdict": report["verdict"],
        "actions": report["tick"]["actions"],
        "execute_ready": report["tick"]["execute_ready"],
        "risks": report["risks"],
        "handoff_drafts": report["handoff_drafts"],
        "execution": report["execution"],
        "note": "Nothing was dispatched. Execute only an action whose "
                "execute_allowed is true, through the guarded takeover command.",
    }
