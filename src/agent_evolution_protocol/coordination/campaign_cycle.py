"""Event-driven, model-free campaign coordination. Never submits an artifact.

This module owns assignment/ACK/result state, not a client's chat UI. A mailbox
entry is durable; a wake request is best effort; neither counts as acceptance.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from agent_evolution_protocol.coordination import bridge


NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
TASK = re.compile(r"^T[0-9]+$")
SHA = re.compile(r"^[0-9a-f]{64}$")
ROLES = {"lead", "deputy", "cross_platform", "catfish", "reviewer", "rescue"}
TERMINAL = {"completed", "failed", "blocked", "timed_out"}
MAX_IN_FLIGHT_PER_AGENT = 2


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return now().isoformat(timespec="seconds")


def event_id() -> str:
    return now().strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]


def _name(value: str) -> str:
    if not NAME.fullmatch(value):
        raise ValueError(f"invalid agent: {value}")
    return value


def _task(value: str) -> str:
    value = value.upper()
    if not TASK.fullmatch(value):
        raise ValueError(f"invalid task: {value}")
    return value


def _state_path(root: Path, task: str) -> Path:
    return Path(root) / "campaign" / "missions" / f"{_task(task)}.json"


def _atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_once(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def _lock(root: Path, task: str):
    path = Path(root) / "campaign" / "locks" / f"{_task(task)}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read(root: Path, task: str) -> dict:
    path = _state_path(root, task)
    if not path.is_file():
        raise ValueError(f"mission not found: {task}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save(root: Path, state: dict, kind: str, detail: dict) -> None:
    state["updated_at"] = stamp()
    _atomic(_state_path(root, state["task"]), state)
    event = {"id": event_id(), "at": stamp(), "task": state["task"], "kind": kind, **detail}
    _write_once(Path(root) / "campaign" / "events" / f"{event['id']}.json", event)


def _session(bridge_root: Path, agent: str) -> dict:
    path = bridge_root / "sessions" / f"{agent}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"agent": agent, "mode": "sticky", "new_session_policy": "forbid-by-default"}


def _notify(root: Path, bridge_root: Path, state: dict, item: dict) -> None:
    agent = item["agent"]
    entry = str(_state_path(root, state["task"]).resolve())
    text = (f"作战任务 {state['task']} / {item['role']} / 事件 {item['id']}\n"
            f"目标: {item['objective']}\n证据入口: {entry}\n"
            f"请在截止前明确 ACK；给出假设、最小实验、失败签名和证据。"
            f"不盲投、不新建会话；收到消息不等于接单。")
    message = {"id": item["id"], "at": stamp(), "sender": "campaign", "to": agent,
               "topic": state["task"], "kind": "assignment", "text": text}
    message_path = bridge_root / "messages" / agent / f"{item['id']}.json"
    if not message_path.exists():
        _write_once(message_path, message)
    adapter = Path(__file__).resolve().parent / "wake_adapters" / f"{agent}.ps1"
    profile = Path(root) / "ui_profiles" / f"{agent}.json"
    if not adapter.is_file() and not profile.is_file():
        item["wake_status"] = "NO_ADAPTER_MAILBOX_ONLY"
        return
    wake = {"id": item["id"], "type": "wake_request", "at": stamp(), "sender": "campaign",
            "to": agent, "task": state["task"], "reason": f"{item['role']}: {item['objective'][:100]}",
            "entrypoint": entry, "no_polling": True, "dry_run": False,
            "session": _session(bridge_root, agent), "allow_new_session": False,
            "claim_timeout_seconds": item["ack_seconds"]}
    wake_path = bridge_root / "wake_queue" / f"{item['id']}.json"
    handled = any((bridge_root / folder / f"{item['id']}.json").exists()
                  for folder in ("wake_done", "wake_failed", "wake_pending"))
    if not wake_path.exists() and not handled:
        _write_once(wake_path, wake)
    item["wake_status"] = "HANDLED_UNVERIFIED" if handled else "QUEUED_UNVERIFIED"


def _assign_locked(root: Path, bridge_root: Path, state: dict, *, agent: str,
                   role: str, objective: str, cause: str, ack_seconds: int = 600,
                   work_seconds: int = 7200, when: datetime | None = None) -> dict:
    _name(agent)
    if role not in ROLES or not objective.strip():
        raise ValueError("invalid role or empty objective")
    if not 30 <= ack_seconds <= 86400 or not 60 <= work_seconds <= 604800:
        raise ValueError("invalid deadline")
    existing = next((row for row in state["assignments"] if row["cause"] == cause and row["agent"] == agent), None)
    if existing:
        return existing
    when = when or now()
    in_flight = sum(row["agent"] == agent and row["status"] in {"awaiting_ack", "active"}
                    for row in state["assignments"])
    initial_status = "queued_capacity" if in_flight >= MAX_IN_FLIGHT_PER_AGENT else "awaiting_ack"
    item = {"id": event_id(), "agent": agent, "role": role, "objective": objective.strip(),
            "cause": cause, "status": initial_status, "created_at": when.isoformat(),
            "ack_due_at": (when + timedelta(seconds=ack_seconds)).isoformat(),
            "work_seconds": work_seconds, "ack_seconds": ack_seconds,
            "wake_status": "DEFERRED" if initial_status == "queued_capacity" else "PENDING"}
    state["assignments"].append(item)
    _save(root, state, "ASSIGNED", {"assignment_id": item["id"], "agent": agent,
                                   "role": role, "cause": cause})
    if initial_status == "awaiting_ack":
        _notify(root, bridge_root, state, item)
        _atomic(_state_path(root, state["task"]), state)
    return item


def open_mission(root: Path, bridge_root: Path, *, task: str, goal: str, lead: str,
                 deputy: str, cross_platform: str, catfish: str, reviewer: str,
                 context: list[str] | None = None) -> dict:
    task = _task(task)
    agents = [lead, deputy, cross_platform, catfish, reviewer]
    for agent in agents:
        _name(agent)
    if (len(set(agents)) < 4 or lead == deputy or reviewer in {lead, deputy}
            or cross_platform == catfish or not goal.strip()):
        raise ValueError("mission needs a goal and independent lead, deputy, reviewer and challenge roles")
    context = context or []
    if any(not value.strip() for value in context):
        raise ValueError("empty context reference")
    with _lock(root, task):
        if _state_path(root, task).exists():
            raise ValueError("mission already exists; use a result or assignment event")
        state = {"task": task, "goal": goal.strip(), "team": dict(lead=lead, deputy=deputy,
                 cross_platform=cross_platform, catfish=catfish, reviewer=reviewer),
                 "context": context, "assignments": [], "returns": [], "holds": [],
                 "opened_at": stamp(), "updated_at": stamp(), "upload_authorized": False}
        _save(root, state, "MISSION_OPENED", {"team": state["team"]})
        for role in ("lead", "cross_platform", "catfish"):
            objective = {"lead": f"推进主方案：{goal}",
                         "cross_platform": f"审查跨芯编译与精度风险：{goal}",
                         "catfish": f"提出可证伪的异路方案：{goal}"}[role]
            _assign_locked(root, bridge_root, state, agent=state["team"][role], role=role,
                           objective=objective, cause=f"opening:{role}")
        return state


def assign(root: Path, bridge_root: Path, *, task: str, agent: str, role: str,
           objective: str, cause: str, ack_seconds: int = 600, work_seconds: int = 7200) -> dict:
    with _lock(root, task):
        state = _read(root, task)
        return _assign_locked(root, bridge_root, state, agent=agent, role=role,
                              objective=objective, cause=cause, ack_seconds=ack_seconds,
                              work_seconds=work_seconds)


def ack(root: Path, *, task: str, assignment_id: str, agent: str) -> dict:
    with _lock(root, task):
        state = _read(root, task)
        item = next((row for row in state["assignments"] if row["id"] == assignment_id), None)
        if not item or item["agent"] != _name(agent) or item["status"] != "awaiting_ack":
            raise ValueError("assignment not awaiting this agent's ACK")
        if now() > datetime.fromisoformat(item["ack_due_at"]):
            raise ValueError("ACK deadline passed; reconcile and reassign")
        item["status"] = "active"
        item["acked_at"] = stamp()
        item["work_due_at"] = (now() + timedelta(seconds=item["work_seconds"])).isoformat()
        _save(root, state, "AGENT_ACK", {"assignment_id": assignment_id, "agent": agent})
        return item


def result(root: Path, bridge_root: Path, *, task: str, assignment_id: str, agent: str,
           outcome: str, evidence: list[str], hypothesis: str, next_step: str) -> dict:
    if outcome not in {"completed", "failed", "blocked"} or not evidence or any(not x.strip() for x in evidence):
        raise ValueError("result needs outcome and evidence references")
    if not hypothesis.strip() or not next_step.strip():
        raise ValueError("result needs hypothesis and next step")
    with _lock(root, task):
        state = _read(root, task)
        item = next((row for row in state["assignments"] if row["id"] == assignment_id), None)
        if not item or item["agent"] != _name(agent) or item["status"] != "active":
            raise ValueError("only an active assignee can report result")
        item["status"] = outcome
        item["finished_at"] = stamp()
        item["evidence"] = evidence
        item["hypothesis"] = hypothesis.strip()
        item["next_step"] = next_step.strip()
        _save(root, state, "EVIDENCE_RESULT", {"assignment_id": assignment_id,
                 "agent": agent, "outcome": outcome, "evidence": evidence})
        if outcome in {"failed", "blocked"} and item["role"] == "lead":
            _assign_locked(root, bridge_root, state, agent=state["team"]["deputy"],
                           role="rescue", objective=f"接力主攻：{next_step}",
                           cause=f"lead-failed:{assignment_id}")
        elif outcome == "completed" and item["role"] in {"lead", "rescue"}:
            _assign_locked(root, bridge_root, state, agent=state["team"]["reviewer"],
                           role="reviewer", objective=f"独立审阅主攻成果与下一步：{next_step}；核查证据 {', '.join(evidence)}",
                           cause=f"lead-completed:{assignment_id}")
        return item


def official_return(root: Path, bridge_root: Path, *, task: str, submission_id: str,
                    package_sha256: str, snapshot_path: Path) -> dict:
    if not SHA.fullmatch(package_sha256):
        raise ValueError("package SHA-256 required")
    snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    if str(snapshot.get("submission_id")) != submission_id or not isinstance(snapshot.get("chips"), dict):
        raise ValueError("official snapshot needs matching submission_id and chips")
    if not snapshot["chips"]:
        raise ValueError("empty chip results")
    with _lock(root, task):
        state = _read(root, task)
        previous = next((row for row in state["returns"] if row["submission_id"] == submission_id), None)
        if previous:
            if previous["package_sha256"] != package_sha256:
                raise ValueError("submission id reused with a different package")
            entry = previous
        else:
            failures = []
            for chip, row in snapshot["chips"].items():
                if row.get("status") == "failed":
                    signature = str(row.get("failure_signature", "")).strip()
                    if not signature or not (row.get("raw_error") or row.get("raw_error_path")):
                        raise ValueError(f"failed chip {chip} needs signature and raw error")
                    failures.append({"chip": chip, "signature": signature})
                elif row.get("status") not in {"passed", "pending"}:
                    raise ValueError(f"invalid chip status: {chip}")
            entry = {"submission_id": submission_id, "package_sha256": package_sha256,
                     "snapshot_path": str(Path(snapshot_path).resolve()), "at": stamp(),
                     "failures": failures, "verified_official": False}
            state["returns"].append(entry)
            _save(root, state, "RESULT_RECORDED", {"submission_id": submission_id,
                     "failures": failures, "note": "snapshot supplied locally; authenticity not independently verified"})
        for failure in entry["failures"]:
            chip, signature = failure["chip"], failure["signature"]
            repeated = sum(1 for row in state["returns"] for issue in row["failures"]
                           if issue["chip"] == chip and issue["signature"] == signature)
            cause = f"return:{submission_id}:{chip}:{signature}"
            if repeated >= 2:
                hold = {"chip": chip, "signature": signature, "at": stamp(),
                        "reason": "repeat failure; reviewed fix and regression required before upload"}
                if not any(x["chip"] == chip and x["signature"] == signature for x in state["holds"]):
                    state["holds"].append(hold)
                    _save(root, state, "CEASEFIRE", hold)
            for role, objective in (
                ("cross_platform", f"会诊 {chip} 的 {signature}；拆核/编译/精度最小实验"),
                ("catfish", f"反证 {chip} 的 {signature} 主流解释，给出异路预测"),
                ("lead", f"整合 {chip} 的 {signature} 双路线证据并规划下一包"),
            ):
                _assign_locked(root, bridge_root, state, agent=state["team"][role],
                               role=role, objective=objective, cause=f"{cause}:{role}")
        return entry


def reconcile(root: Path, bridge_root: Path, *, at: datetime | None = None) -> dict:
    at = at or now()
    actions = []
    for path in sorted((Path(root) / "campaign" / "missions").glob("T*.json")):
        task = path.stem
        with _lock(root, task):
            state = _read(root, task)
            for item in list(state["assignments"]):
                if item.get("wake_status") == "NO_ADAPTER_MAILBOX_ONLY":
                    adapter = Path(__file__).resolve().parent / "wake_adapters" / f"{item['agent']}.ps1"
                    profile = Path(root) / "ui_profiles" / f"{item['agent']}.json"
                    if (adapter.is_file() or profile.is_file()) and item["status"] in {"awaiting_ack", "active"}:
                        item["wake_status"] = "PENDING"
                if item["status"] == "queued_capacity":
                    in_flight = sum(row["agent"] == item["agent"] and row["status"] in {"awaiting_ack", "active"}
                                    for row in state["assignments"])
                    if in_flight < MAX_IN_FLIGHT_PER_AGENT:
                        item["status"] = "awaiting_ack"
                        item["ack_due_at"] = (at + timedelta(seconds=item["ack_seconds"])).isoformat()
                        item["wake_status"] = "PENDING"
                        _save(root, state, "CAPACITY_RELEASED", {"assignment_id": item["id"], "agent": item["agent"]})
                        actions.append({"task": task, "assignment_id": item["id"], "event": "capacity_released"})
                if item.get("wake_status") == "PENDING":
                    _notify(root, bridge_root, state, item)
                    _atomic(_state_path(root, task), state)
                    actions.append({"task": task, "assignment_id": item["id"], "event": "notification_repaired"})
                if item["status"] == "awaiting_ack" and at > datetime.fromisoformat(item["ack_due_at"]):
                    item["status"] = "timed_out"
                    _save(root, state, "ACK_TIMEOUT", {"assignment_id": item["id"], "agent": item["agent"]})
                elif item["status"] == "active" and at > datetime.fromisoformat(item["work_due_at"]):
                    item["status"] = "timed_out"
                    _save(root, state, "WORK_TIMEOUT", {"assignment_id": item["id"], "agent": item["agent"]})
                else:
                    continue
                actions.append({"task": task, "assignment_id": item["id"], "event": "timed_out"})
                if item["agent"] != state["team"]["deputy"]:
                    rescue = _assign_locked(root, bridge_root, state, agent=state["team"]["deputy"],
                                            role="rescue", objective=f"超时接力：{item['objective']}",
                                            cause=f"timeout:{item['id']}", when=at)
                    actions.append({"task": task, "assignment_id": rescue["id"], "event": "rescue_assigned"})
    return {"at": at.isoformat(), "actions": actions, "model_calls": 0}


def status(root: Path, task: str = "") -> dict:
    paths = [_state_path(root, task)] if task else sorted((Path(root) / "campaign" / "missions").glob("T*.json"))
    missions = []
    for path in paths:
        if path.is_file():
            state = json.loads(path.read_text(encoding="utf-8"))
            missions.append(state)
    return {"missions": missions, "next_due_at": next_due(root),
            "note": "Mailbox/wake is not ACK; all uploads remain independently gated."}


def scoreboard(root: Path) -> dict:
    """Evidence-weighted contribution, not message volume or online time."""
    rows: dict[str, dict] = {}
    for mission in status(root)["missions"]:
        for item in mission["assignments"]:
            agent = item["agent"]
            row = rows.setdefault(agent, {"agent": agent, "score": 0, "evidence_results": 0,
                                          "active": 0, "awaiting_ack": 0, "timeouts": 0})
            if item["status"] in {"completed", "failed", "blocked"} and item.get("evidence"):
                row["evidence_results"] += 1
                row["score"] += {"completed": 3, "failed": 1, "blocked": 0}[item["status"]]
            elif item["status"] == "timed_out":
                row["timeouts"] += 1
                row["score"] -= 1
            elif item["status"] in {"active", "awaiting_ack"}:
                row[item["status"]] += 1
    return {"rows": sorted(rows.values(), key=lambda row: (-row["score"], row["agent"])),
            "note": "A failed but evidenced experiment can falsify a bad path. Scores do not authorize uploads or punish justified blockers."}


def next_due(root: Path) -> str:
    times = []
    for path in (Path(root) / "campaign" / "missions").glob("T*.json"):
        state = json.loads(path.read_text(encoding="utf-8"))
        for item in state.get("assignments", []):
            key = "ack_due_at" if item["status"] == "awaiting_ack" else "work_due_at" if item["status"] == "active" else ""
            if key and item.get(key):
                times.append(item[key])
    return min(times) if times else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("open", "assign", "ack", "result", "official-return", "reconcile", "status", "scoreboard", "next-due"))
    parser.add_argument("--root", type=Path, default=Path.cwd() / "coordination")
    parser.add_argument("--bridge-root", type=Path, default=bridge.ROOT)
    for name in ("task", "goal", "lead", "deputy", "cross-platform", "catfish", "reviewer",
                 "agent", "role", "objective", "cause", "assignment-id", "outcome",
                 "hypothesis", "next-step", "submission-id", "package-sha256"):
        parser.add_argument("--" + name, default="")
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--snapshot-path", type=Path)
    parser.add_argument("--ack-seconds", type=int, default=600)
    parser.add_argument("--work-seconds", type=int, default=7200)
    args = parser.parse_args()
    try:
        if args.action == "open":
            output = open_mission(args.root, args.bridge_root, task=args.task, goal=args.goal,
                lead=args.lead, deputy=args.deputy, cross_platform=args.cross_platform,
                catfish=args.catfish, reviewer=args.reviewer, context=args.context)
        elif args.action == "assign":
            output = assign(args.root, args.bridge_root, task=args.task, agent=args.agent,
                role=args.role, objective=args.objective, cause=args.cause,
                ack_seconds=args.ack_seconds, work_seconds=args.work_seconds)
        elif args.action == "ack":
            output = ack(args.root, task=args.task, assignment_id=args.assignment_id, agent=args.agent)
        elif args.action == "result":
            output = result(args.root, args.bridge_root, task=args.task, assignment_id=args.assignment_id,
                agent=args.agent, outcome=args.outcome, evidence=args.evidence,
                hypothesis=args.hypothesis, next_step=args.next_step)
        elif args.action == "official-return":
            output = official_return(args.root, args.bridge_root, task=args.task,
                submission_id=args.submission_id, package_sha256=args.package_sha256,
                snapshot_path=args.snapshot_path)
        elif args.action == "reconcile":
            output = reconcile(args.root, args.bridge_root)
        elif args.action == "next-due":
            output = {"next_due_at": next_due(args.root)}
        elif args.action == "scoreboard":
            output = scoreboard(args.root)
        else:
            output = status(args.root, args.task)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
