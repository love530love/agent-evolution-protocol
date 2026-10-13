"""Any freshly present teammate may request a bounded, auditable handoff.

An agent only needs to write one JSON request to delegation/inbox. The local
watcher validates it, deposits the full order, then queues the best available
wake adapter. Window focus and mailbox delivery never count as target ACK.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

from . import bridge, conversation_journal, presence

NAME = presence.NAME
REQUEST_ID = re.compile(r"^D[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$")
TASK = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
PRIORITIES = {"normal", "urgent"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _path(root: Path, group: str, name: str) -> Path:
    return Path(root) / "delegation" / group / f"{name}.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _presence(root: Path, agent: str, session_id: str = "") -> dict:
    row = presence._read(Path(root) / "presence" / "agents" / f"{agent}.json")
    if presence.effective(row) != "online":
        raise ValueError(f"SENDER_NOT_ONLINE: {agent}; signal presence before commanding")
    if row.get("session_id") and row["session_id"] != session_id:
        raise ValueError("SENDER_SESSION_MISMATCH")
    return row


def create(root: Path, *, sender: str, to: str, task: str, instruction: str,
           evidence: list[str], session_id: str = "", priority: str = "normal",
           reply_minutes: int = 30) -> dict:
    if not NAME.fullmatch(sender) or not NAME.fullmatch(to) or sender == to:
        raise ValueError("two different valid agent names required")
    if not TASK.fullmatch(task) or not instruction.strip() or len(instruction) > 8000:
        raise ValueError("valid task and 1..8000 character instruction required")
    if priority not in PRIORITIES or not 1 <= reply_minutes <= 120:
        raise ValueError("priority normal/urgent and reply deadline 1..120 minutes required")
    if not evidence or any(not value.strip() for value in evidence):
        raise ValueError("at least one evidence reference required")
    _presence(root, sender, session_id)
    at = _now()
    event = {"id": "D" + at.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:12],
             "from": sender, "to": to, "task": task, "instruction": instruction.strip(),
             "evidence": evidence, "priority": priority, "session_id": session_id,
             "created_at": at.isoformat(),
             "reply_due_at": (at + timedelta(minutes=reply_minutes)).isoformat(),
             "allow_new_session": False,
             "note": "Request is not an ACK or proof of delivery."}
    bridge.write(_path(root, "inbox", event["id"]), event)
    return event


def _validate_request(root: Path, request: dict, filename: str) -> None:
    event_id = request.get("id", "")
    sender = request.get("from", "")
    target = request.get("to", "")
    if not isinstance(event_id, str) or not REQUEST_ID.fullmatch(event_id) or filename != event_id:
        raise ValueError("INVALID_REQUEST_ID")
    if (not isinstance(sender, str) or not NAME.fullmatch(sender) or
            not isinstance(target, str) or not NAME.fullmatch(target) or sender == target):
        raise ValueError("INVALID_AGENTS")
    if not TASK.fullmatch(str(request.get("task", ""))):
        raise ValueError("INVALID_TASK")
    instruction = request.get("instruction", "")
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 8000:
        raise ValueError("INVALID_INSTRUCTION")
    if (not isinstance(request.get("evidence"), list) or not request["evidence"] or
            len(request["evidence"]) > 20 or any(not isinstance(value, str) or not value.strip() or
                                                   len(value) > 1000 for value in request["evidence"])):
        raise ValueError("EVIDENCE_REQUIRED")
    if request.get("priority") not in PRIORITIES:
        raise ValueError("INVALID_PRIORITY")
    if request.get("allow_new_session") is not False:
        raise ValueError("NEW_SESSION_NOT_AUTHORISED")
    _presence(root, sender, str(request.get("session_id", "")))
    try:
        due = datetime.fromisoformat(request["reply_due_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("INVALID_DEADLINE") from exc
    if due.tzinfo is None or not _now() < due <= _now() + timedelta(hours=3):
        raise ValueError("EXPIRED_OR_EXCESSIVE_DEADLINE")


def process_one(root: Path, bridge_root: Path, request_path: Path) -> dict:
    root = Path(root)
    bridge_root = Path(bridge_root)
    with presence._lock(root):
        event_id = request_path.stem
        receipt_path = _path(root, "receipts", event_id)
        if receipt_path.exists():
            return _read(receipt_path)
        try:
            request = _read(request_path)
            _validate_request(root, request, event_id)
        except (OSError, ValueError, TypeError) as exc:
            receipt = {"id": event_id, "state": "REJECTED", "reason": str(exc)[:500],
                       "at": _now().isoformat(), "agent_accepted": False}
            bridge.write(receipt_path, receipt)
            return receipt
        target = request["to"]
        sender = request["from"]
        text = (f"协作请求 {event_id}，来自 {sender}；任务 {request['task']}，优先级 {request['priority']}。\n"
                f"指令：{request['instruction']}\n证据：" + "\n".join(request["evidence"]) +
                f"\n请在 {request['reply_due_at']} 前以同一事件 ID 明确 ACK/拒绝、说明判断和下一步。"
                "保持原会话和模型；不要把收到消息当成已接单。")
        mailbox = {"id": event_id, "at": request["created_at"], "sender": sender,
                   "to": target, "topic": request["task"], "kind": "delegation", "text": text}
        message_path = bridge_root / "messages" / target / f"{event_id}.json"
        if not message_path.exists():
            bridge.write(message_path, mailbox)
        adapter = Path(__file__).resolve().parent / "wake_adapters" / f"{target}.ps1"
        profile = root / "ui_profiles" / f"{target}.json"
        has_adapter = adapter.is_file() or profile.is_file()
        relay = ""
        if has_adapter:
            wake = {"id": event_id, "type": "wake_request", "at": request["created_at"],
                    "sender": sender, "to": target, "task": request["task"],
                    "reason": request["instruction"][:200], "entrypoint": str(request_path.resolve()),
                    "deliver_text": text, "no_polling": True, "dry_run": False,
                    "allow_new_session": False,
                    "session": {"mode": "sticky", "new_session_policy": "forbid-by-default"},
                    "claim_timeout_seconds": max(60, int((datetime.fromisoformat(request["reply_due_at"]) - _now()).total_seconds()))}
            wake_path = bridge_root / "wake_queue" / f"{event_id}.json"
            if not wake_path.exists():
                bridge.write(wake_path, wake)
        else:
            # One hop only: an online teammate with a configured transport can
            # inspect the problem and relay through an authorised channel. It
            # must report back; this is never mistaken for target acceptance.
            for candidate in presence.snapshot(root)["agents"]:
                name = candidate["agent"]
                if name in {sender, target} or candidate["state"] != "online":
                    continue
                candidate_adapter = adapter.with_name(f"{name}.ps1")
                candidate_profile = root / "ui_profiles" / f"{name}.json"
                if candidate_adapter.is_file() or candidate_profile.is_file():
                    relay = name
                    break
            if relay:
                relay_id = event_id + "-relay"
                relay_text = (f"临时中继请求 {relay_id}：{sender} 需要联系 {target}，但目标没有已验收唤醒适配器。"
                              f"请仅通过你已获授权的通道传递原指令并要求目标明确 ACK；无法验证则报告阻塞。\n{text}")
                relay_mail = {"id": relay_id, "at": request["created_at"], "sender": sender,
                              "to": relay, "topic": request["task"], "kind": "relay_request",
                              "text": relay_text, "reply_to": event_id}
                relay_mail_path = bridge_root / "messages" / relay / f"{relay_id}.json"
                if not relay_mail_path.exists():
                    bridge.write(relay_mail_path, relay_mail)
                relay_wake_path = bridge_root / "wake_queue" / f"{relay_id}.json"
                if not relay_wake_path.exists():
                    bridge.write(relay_wake_path, {"id": relay_id, "type": "wake_request",
                                                   "at": request["created_at"], "sender": sender,
                                                   "to": relay, "task": request["task"],
                                                   "reason": f"relay to {target}", "entrypoint": str(request_path.resolve()),
                                                   "deliver_text": relay_text, "no_polling": True,
                                                   "dry_run": False, "allow_new_session": False,
                                                   "session": {"mode": "sticky", "new_session_policy": "forbid-by-default"},
                                                   "claim_timeout_seconds": 600})
        state = ("WAKE_QUEUED_UNVERIFIED" if has_adapter else
                 "RELAY_QUEUED_UNVERIFIED" if relay else "MAILBOX_ONLY")
        receipt = {"id": event_id, "state": state,
                   "from": sender, "to": target, "task": request["task"],
                   "message_path": str(message_path), "wake_queued": has_adapter,
                   "relay": relay,
                   "agent_accepted": False, "at": _now().isoformat(),
                   "next": "await_target_ack" if has_adapter else
                           "await_relay_then_target_ack" if relay else "needs_adapter_or_manual_relay"}
        bridge.write(receipt_path, receipt)
        conversation_journal.append_event(root, room="agent-legion", actor=sender,
                                          source="team-dispatch", kind="message", text=text)
        return receipt


def process(root: Path, bridge_root: Path) -> dict:
    receipts = []
    for path in sorted((Path(root) / "delegation" / "inbox").glob("*.json")):
        if not _path(root, "receipts", path.stem).exists():
            receipts.append(process_one(root, bridge_root, path))
    return {"processed": receipts}


def acknowledge(root: Path, bridge_root: Path, *, event_id: str, agent: str,
                session_id: str, reply: str, accepted: bool) -> dict:
    if not REQUEST_ID.fullmatch(event_id) or not NAME.fullmatch(agent) or not reply.strip():
        raise ValueError("valid event, agent and reply required")
    _presence(root, agent, session_id)
    with presence._lock(Path(root)):
        receipt_path = _path(root, "receipts", event_id)
        receipt = _read(receipt_path)
        if not receipt or receipt.get("to") != agent or receipt.get("state") == "REJECTED":
            raise ValueError("no matching dispatch")
        if receipt.get("state") in {"AGENT_ACK", "AGENT_DECLINED"}:
            return receipt
        receipt.update(state="AGENT_ACK" if accepted else "AGENT_DECLINED",
                       agent_accepted=accepted, reply=reply, replied_at=_now().isoformat())
        bridge.write(receipt_path, receipt)
        response_id = event_id + "-reply"
        response = {"id": response_id, "at": receipt["replied_at"],
                    "sender": agent, "to": receipt["from"], "topic": receipt["task"],
                    "kind": "delegation_reply", "text": f"协作回执 {response_id}：{reply}", "reply_to": event_id,
                    "accepted": accepted}
        response_path = Path(bridge_root) / "messages" / receipt["from"] / f"{response_id}.json"
        if not response_path.exists():
            bridge.write(response_path, response)
        sender_adapter = Path(__file__).resolve().parent / "wake_adapters" / f"{receipt['from']}.ps1"
        sender_profile = Path(root) / "ui_profiles" / f"{receipt['from']}.json"
        if sender_adapter.is_file() or sender_profile.is_file():
            wake_path = Path(bridge_root) / "wake_queue" / f"{response_id}.json"
            if not wake_path.exists():
                bridge.write(wake_path, {"id": response_id, "type": "wake_request",
                                         "at": receipt["replied_at"], "sender": agent,
                                         "to": receipt["from"], "task": receipt["task"],
                                         "reason": "collaboration reply available",
                                         "entrypoint": str(receipt_path),
                                         "deliver_text": response["text"], "no_polling": True,
                                         "dry_run": False, "allow_new_session": False,
                                         "session": {"mode": "sticky", "new_session_policy": "forbid-by-default"},
                                         "claim_timeout_seconds": 600})
        conversation_journal.append_event(root, room="agent-legion", actor=agent,
                                          source="team-dispatch", kind="ack" if accepted else "warning",
                                          text=f"{event_id}: {reply}")
        return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("request", "process", "ack", "decline", "status"))
    parser.add_argument("--root", type=Path, default=Path.cwd() / "coordination")
    parser.add_argument("--bridge-root", type=Path, default=bridge.ROOT)
    parser.add_argument("--from", dest="sender", default="")
    parser.add_argument("--to", default="")
    parser.add_argument("--task", default="")
    parser.add_argument("--instruction", default="")
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--session-id", default="")
    parser.add_argument("--priority", choices=sorted(PRIORITIES), default="normal")
    parser.add_argument("--reply-minutes", type=int, default=30)
    parser.add_argument("--event-id", default="")
    parser.add_argument("--agent", default="")
    parser.add_argument("--reply", default="")
    args = parser.parse_args()
    try:
        if args.action == "request":
            result = create(args.root, sender=args.sender, to=args.to, task=args.task,
                            instruction=args.instruction, evidence=args.evidence,
                            session_id=args.session_id, priority=args.priority,
                            reply_minutes=args.reply_minutes)
            result = {"request": result, "dispatch": process_one(args.root, args.bridge_root,
                                                                  _path(args.root, "inbox", result["id"]))}
        elif args.action == "process":
            result = process(args.root, args.bridge_root)
        elif args.action in {"ack", "decline"}:
            result = acknowledge(args.root, args.bridge_root, event_id=args.event_id,
                                 agent=args.agent, session_id=args.session_id,
                                 reply=args.reply, accepted=args.action == "ack")
        else:
            result = _read(_path(args.root, "receipts", args.event_id))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
