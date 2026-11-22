"""Guarded real dispatch for coalition handoffs.

Planning is read-only; this module is the only place a coalition decision can
turn into a wake request.  It re-plans at the moment of dispatch, refuses any
action the plan does not currently recommend, and writes one durable handoff
record per ``HO-`` key.  A queued wake, a mailbox deposit and an agent ACK stay
three separate evidence levels; none of them is reported as delivery success.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from ..coordination import team_dispatch
from . import io, planner, spec as _spec

SCHEMA = "aep-coalition-handoff-v1"
DIRECTORY = "coalition/handoffs"
EXECUTABLE_ACTIONS = ("TAKEOVER_NOW", "ASSIGN_PRIMARY")
ALLOWED_TRIGGERS = ("bootstrap", "quota_exhausted", "credit_exhausted",
                    "usage_limit", "time_limit", "credit_warning", "offline",
                    "capability_mismatch", "holder_unverified")
_AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_KEY = re.compile(r"^HO-[0-9a-f]{16}$")

_RECEIPT_STATES = (
    "MAILBOX_ONLY", "WAKE_QUEUED_UNVERIFIED", "RELAY_QUEUED_UNVERIFIED",
    "REJECTED", "AGENT_ACK", "AGENT_DECLINED",
)


def _directory(coord_root):
    return Path(coord_root) / DIRECTORY


def _record_path(coord_root, key):
    return _directory(coord_root) / f"{key}.json"


def _snapshot_path(coord_root, key):
    return _directory(coord_root) / f"{key}.plan.json"


def _clean_agent(value, label):
    if not isinstance(value, str) or not _AGENT.fullmatch(value.strip()):
        raise ValueError(f"INVALID_{label}")
    return value.strip()


def _clean_key(value):
    if not isinstance(value, str) or not _KEY.fullmatch(value.strip()):
        raise ValueError("INVALID_HANDOFF_KEY")
    return value.strip()


def _clean_evidence(evidence):
    if isinstance(evidence, str):
        items = [evidence]
    elif isinstance(evidence, (list, tuple)):
        items = list(evidence)
    else:
        raise ValueError("EVIDENCE_REQUIRED")
    cleaned = [item.strip() for item in items
               if isinstance(item, str) and item.strip()]
    if not cleaned:
        raise ValueError("EVIDENCE_REQUIRED")
    return cleaned


def _role_source(mission, role_id):
    for role in mission["roles"]:
        if role["id"] == role_id:
            return role
    raise ValueError(f"UNKNOWN_ROLE:{role_id}")


def _require_same_handoff(existing, mission, role_id, to_agent, trigger, from_agent):
    problems = []
    for label, actual, expected in (
            ("MISSION", existing.get("mission"), mission),
            ("ROLE", existing.get("role"), role_id),
            ("TARGET", existing.get("to"), to_agent),
            ("TRIGGER", existing.get("trigger"), trigger),
            ("FROM", existing.get("from"), from_agent)):
        if actual != expected:
            problems.append(label)
    if problems:
        raise ValueError("HANDOFF_RECORD_CONFLICT:" + ",".join(problems))


def _existing_record(coord_root, key):
    path = _record_path(coord_root, key)
    if not path.is_file():
        return None
    try:
        row = io.read_json(path)
    except (OSError, ValueError) as exc:
        raise ValueError("HANDOFF_RECORD_UNREADABLE") from exc
    if not isinstance(row, dict):
        raise ValueError("HANDOFF_RECORD_UNREADABLE")
    return row


def _guard(report, role_id, to_agent, trigger, from_agent=None, authority=None):
    if role_id not in report["roles"]:
        raise ValueError(f"UNKNOWN_ROLE:{role_id}")
    role = report["roles"][role_id]
    tick = role["tick"]
    problems = []
    if tick["action"] not in EXECUTABLE_ACTIONS:
        problems.append(f"ACTION_NOT_EXECUTABLE:{tick['action']}")
    if tick["target"] != to_agent:
        problems.append("TARGET_NOT_RECOMMENDED")
    if tick["trigger"] != trigger:
        problems.append("TRIGGER_NOT_RECOMMENDED")
    if trigger not in ALLOWED_TRIGGERS:
        problems.append("UNKNOWN_TRIGGER")
    current_from = tick["from"]
    if from_agent is not None and from_agent != current_from:
        problems.append("FROM_MISMATCH")
    expected_authority = tick["authority"]
    if authority is not None and authority != expected_authority:
        problems.append("AUTHORITY_MISMATCH")
    if trigger == "bootstrap" and current_from is not None:
        problems.append("BOOTSTRAP_WITH_HOLDER")
    if tick["requires_verification"]:
        problems.append("VERIFICATION_REQUIRED_FIRST")
    return {
        "ok": not problems,
        "problems": problems,
        "role": role_id,
        "action": tick["action"],
        "reason": tick["reason"],
        "from": current_from,
        "to": tick["target"],
        "trigger": tick["trigger"],
        "authority": expected_authority,
        "handoff_key": tick["handoff_key"],
        "plan": {"verdict": report["verdict"],
                 "input_digest": report["input_digest"],
                 "plan_digest": report["plan_digest"]},
        "execute_allowed": bool(tick["execute_allowed"] and not problems),
    }


def _instruction(report, mission, role, to_agent, trigger, authority, sender,
                 evidence):
    scope = ", ".join(role["write_scope"]) or "none (read/record only)"
    constraints = ", ".join(role["constraints"]) or "none"
    lines = [
        f"你是 {to_agent}。{sender} 通过 AEP 联盟指挥把角色 {role['id']}"
        f"（{role['title']}）交给你。",
        f"任务：{mission['mission']}；关键性：{role['criticality']}；"
        f"触发：{trigger}；权限：{authority}。",
        f"所需能力：{', '.join(role['required_capabilities'])}；"
        f"可降级能力：{', '.join(role['acting_capabilities'])}。",
        f"写入范围：{scope}；额外约束：{constraints}。",
        "强制要求：",
        "1) 保持原会话和模型，不新建会话、不切换模型。",
        "2) 先用本请求事件 ID 明确回复 ACK 或拒绝，说明能否在期限内接手。",
        "3) 只按写入范围执行；scribe 权限只允许记录与转达，禁止不可逆外部写。",
        "4) 不得把收到消息当作已接单；结果不明的提交/上传/发布/删除不得盲目重放。",
        "5) 回执写回发起者信箱并说明证据路径；完成后再报告任务结果。",
        f"规划摘要：verdict={report['verdict']}，"
        f"plan_digest={report['plan_digest'][:16]}。",
        "证据：" + "；".join(evidence),
    ]
    text = "\n".join(lines)
    if len(text) > 8000:
        text = text[:7900] + "\n[truncated]"
    return text


def preview(coord_root, mission_source, *, role, to_agent, trigger,
            from_agent=None, authority=None, now=None):
    """Compare a proposed dispatch against the current plan without writing."""
    report = planner.plan(coord_root, mission_source, now=now)
    mission = _spec.load_spec(mission_source)
    role_id = role
    _role_source(mission, role_id)
    to_agent = _clean_agent(to_agent, "TARGET")
    guard = _guard(report, role_id, to_agent, trigger, from_agent, authority)
    return {
        "schema": "aep-coalition-dispatch-preview-v1",
        "at": report["at"],
        "mission": report["mission"],
        "executed": False,
        "delivery_claim": "NONE",
        "guard": guard,
        "handoff_draft": report["roles"][role_id]["handoff_draft"],
    }


def takeover(coord_root, mission_source, *, role, to_agent, trigger, sender,
             evidence, from_agent=None, authority=None, session_id="",
             reply_minutes=30, task="", bridge_root=None, execute=False,
             now=None):
    """Execute one guarded handoff.  ``execute=False`` is a read-only preview."""
    coord_root = Path(coord_root)
    bridge_root = Path(bridge_root) if bridge_root else coord_root
    report = planner.plan(coord_root, mission_source, now=now)
    mission = _spec.load_spec(mission_source)
    role_id = role
    role_def = _role_source(mission, role_id)
    to_agent = _clean_agent(to_agent, "TARGET")
    guard = _guard(report, role_id, to_agent, trigger, from_agent, authority)
    if not execute:
        return {
            "schema": "aep-coalition-dispatch-preview-v1",
            "at": report["at"],
            "mission": mission["mission"],
            "executed": False,
            "delivery_claim": "NONE",
            "guard": guard,
            "handoff_draft": report["roles"][role_id]["handoff_draft"],
        }
    if not guard["ok"]:
        raise ValueError("GUARD_REFUSED:" + ",".join(guard["problems"]))
    sender = _clean_agent(sender, "SENDER")
    evidence = _clean_evidence(evidence)
    key = guard["handoff_key"]
    if not key:
        raise ValueError("NO_HANDOFF_KEY")

    existing = _existing_record(coord_root, key)
    if existing is not None:
        _require_same_handoff(existing, mission["mission"], role_id, to_agent,
                              trigger, guard["from"])
        if not (existing.get("state") == "NOT_DISPATCHED"
                and not existing.get("request_id")):
            existing = reconcile_one(coord_root, existing)
            existing["idempotent"] = True
            existing["retry_allowed"] = False
            return existing

    snapshot = _snapshot_path(coord_root, key)
    io.atomic_write_json(snapshot, {
        "schema": "aep-coalition-plan-snapshot-v1",
        "key": key,
        "at": report["at"],
        "mission": report["mission"],
        "role": role_id,
        "to": to_agent,
        "trigger": trigger,
        "authority": guard["authority"],
        "plan": report,
        "note": "Snapshot of the plan that authorised this handoff.",
    })
    provenance = [str(snapshot.resolve())]
    if mission_source_path(mission_source):
        provenance.insert(0, str(Path(mission_source).resolve()))
    evidence = evidence[:max(0, 20 - len(provenance))] + provenance

    task = task or f"COALITION-{role_id}"[:79]
    instruction = _instruction(report, mission, role_def, to_agent, trigger,
                               guard["authority"], sender, evidence)
    created_at = io.coerce_utc(now or datetime.now().astimezone(), "now").isoformat()
    record = {
        "schema": SCHEMA,
        "key": key,
        "mission": mission["mission"],
        "role": role_id,
        "from": guard["from"],
        "to": to_agent,
        "trigger": trigger,
        "authority": guard["authority"],
        "action": guard["action"],
        "reason": guard["reason"],
        "created_at": created_at,
        "plan": guard["plan"],
        "snapshot_path": str(snapshot),
        "request_id": None,
        "request_path": None,
        "receipt_path": None,
        "message_path": None,
        "wake_queued": False,
        "relay": "",
        "agent_accepted": False,
        "state": "PENDING",
        "retry_allowed": False,
        "delivery_claim": "NONE",
        "evidence_level": "PLAN_SNAPSHOT_ONLY",
        "next": "await_processing",
        "note": "Mailbox deposit/wake queue are not Agent ACK; reconcile receipts.",
    }
    io.atomic_write_json(_record_path(coord_root, key), record)
    try:
        request = team_dispatch.create(
            coord_root, sender=sender, to=to_agent, task=task,
            instruction=instruction, evidence=evidence, session_id=session_id,
            priority="urgent", reply_minutes=reply_minutes)
    except ValueError as exc:
        # create() validates before writing; a ValueError means no request id
        # exists, so the same key may be retried after the input is corrected.
        record.update(state="NOT_DISPATCHED", retry_allowed=True,
                      next="fix_and_retry",
                      error=f"{type(exc).__name__}: {exc}"[:500],
                      note="No request was created; retry is safe after fixing the error.")
        io.atomic_write_json(_record_path(coord_root, key), record)
        raise ValueError(f"DISPATCH_NOT_SENT: {exc}") from exc
    except BaseException as exc:
        record.update(state="UNCERTAIN", next="inspect_before_any_retry",
                      error=f"{type(exc).__name__}: {exc}"[:500],
                      note="Dispatch outcome is uncertain; never resend blindly.")
        io.atomic_write_json(_record_path(coord_root, key), record)
        raise
    try:
        request_path = coord_root / "delegation" / "inbox" / f"{request['id']}.json"
        record.update(request_id=request["id"], request_path=str(request_path))
        io.atomic_write_json(_record_path(coord_root, key), record)
        receipt = team_dispatch.process_one(coord_root, bridge_root, request_path)
    except BaseException as exc:
        record.update(state="UNCERTAIN", next="inspect_before_any_retry",
                      error=f"{type(exc).__name__}: {exc}"[:500],
                      note="Dispatch outcome is uncertain; never resend blindly.")
        io.atomic_write_json(_record_path(coord_root, key), record)
        raise
    record.update(
        state=receipt.get("state", "UNCERTAIN"),
        receipt_path=str(coord_root / "delegation" / "receipts" /
                         f"{record['request_id']}.json"),
        message_path=receipt.get("message_path"),
        wake_queued=bool(receipt.get("wake_queued")),
        relay=receipt.get("relay") or "",
        agent_accepted=bool(receipt.get("agent_accepted")),
        evidence_level=receipt.get("state", "UNCERTAIN"),
        next=receipt.get("next") or "await_target_ack",
    )
    io.atomic_write_json(_record_path(coord_root, key), record)
    return record


def mission_source_path(mission_source):
    return isinstance(mission_source, (str, Path)) and Path(mission_source).is_file()


def _load_record(coord_root, key):
    try:
        row = io.read_json(_record_path(coord_root, key))
    except (OSError, ValueError):
        return None
    return row if isinstance(row, dict) else None


def reconcile_one(coord_root, record):
    request_id = record.get("request_id")
    if not request_id:
        return record
    receipt_path = Path(coord_root) / "delegation" / "receipts" / f"{request_id}.json"
    try:
        receipt = io.read_json(receipt_path)
    except (OSError, ValueError):
        receipt = None
    if isinstance(receipt, dict):
        record.update(
            state=receipt.get("state", record.get("state")),
            agent_accepted=bool(receipt.get("agent_accepted")),
            evidence_level=receipt.get("state", record.get("evidence_level")),
            reply=receipt.get("reply"),
            replied_at=receipt.get("replied_at"),
            receipt_path=str(receipt_path),
        )
    message_path = record.get("message_path")
    if message_path:
        record["message_present"] = Path(message_path).is_file()
    return record


def reconcile(coord_root, *, key=None):
    """Refresh durable records from delegation receipts; no resend."""
    coord_root = Path(coord_root)
    keys = [key] if key else sorted(
        path.stem for path in _directory(coord_root).glob("HO-*.json")
        if _KEY.fullmatch(path.stem))
    records = []
    for item in keys:
        item = _clean_key(item)
        record = _load_record(coord_root, item)
        if record is None:
            continue
        record = reconcile_one(coord_root, record)
        io.atomic_write_json(_record_path(coord_root, item), record)
        records.append(record)
    return {"schema": "aep-coalition-reconcile-v1",
            "count": len(records), "records": records,
            "note": "Receipts are still not task results; a resend is forbidden."}


def status(coord_root, *, key=None):
    coord_root = Path(coord_root)
    if key:
        record = _load_record(coord_root, _clean_key(key))
        return {"schema": "aep-coalition-handoff-status-v1",
                "records": [record] if record else []}
    records = []
    for path in sorted(_directory(coord_root).glob("HO-*.json")):
        if not _KEY.fullmatch(path.stem):
            continue
        record = _load_record(coord_root, path.stem)
        if record:
            records.append(record)
    return {"schema": "aep-coalition-handoff-status-v1", "records": records}


def acknowledge(coord_root, *, key, agent, session_id, reply, accepted=True):
    """Record an explicit target ACK/decline and update the handoff record."""
    coord_root = Path(coord_root)
    key = _clean_key(key)
    agent = _clean_agent(agent, "AGENT")
    if not isinstance(session_id, str):
        raise ValueError("INVALID_SESSION_ID")
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("REPLY_REQUIRED")
    record = _existing_record(coord_root, key)
    if record is None:
        raise ValueError("HANDOFF_NOT_FOUND")
    request_id = record.get("request_id")
    if not request_id:
        raise ValueError("HANDOFF_NOT_DISPATCHED")
    receipt = team_dispatch.acknowledge(
        coord_root, coord_root, event_id=request_id, agent=agent,
        session_id=session_id, reply=reply, accepted=accepted)
    record = reconcile_one(coord_root, record)
    record.update(state=receipt.get("state", record.get("state")),
                  agent_accepted=bool(receipt.get("agent_accepted")),
                  evidence_level=receipt.get("state", record.get("evidence_level")))
    io.atomic_write_json(_record_path(coord_root, key), record)
    return record
