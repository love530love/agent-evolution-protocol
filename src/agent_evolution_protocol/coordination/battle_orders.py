"""Small natural-language battle orders; local events and mailbox only."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
from uuid import uuid4


ORDER = re.compile(r"^\s*(集结|开火准备|停火|换防|复火)\s+(T[0-9]+)(?:\s*[:：]\s*(.+))?\s*$")
AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _events(root: Path, task: str) -> list[dict]:
    folder = Path(root) / "battle_events" / task
    if not folder.exists():
        return []
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json"))]


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _task_writer_lock(root: Path, task: str):
    path = Path(root) / "battle_events" / task / ".writer.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError("battle order for this task is being written; retry after checking status") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError("battle order for this task is being written; retry after checking status") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def status(root: Path, task: str) -> dict:
    if not re.fullmatch(r"T[0-9]+", task):
        raise ValueError("invalid task")
    lead = ""
    deputy = ""
    holds: dict[str, dict] = {}
    events = _events(root, task)
    for event in events:
        action = event["action"]
        if action == "assemble":
            lead, deputy = event["actor"], event.get("deputy", "")
        elif action == "handoff":
            lead, deputy = event["new_lead"], event["actor"]
        elif action == "ceasefire":
            holds[event["id"]] = {"id": event["id"], "signature": event["signature"], "actor": event["actor"]}
        elif action == "resume":
            holds.pop(event["resolves"], None)
    return {"task": task, "lead": lead, "deputy": deputy,
            "active_holds": list(holds.values()), "event_count": len(events),
            "upload_authorized": False}


def issue(root: Path, *, actor: str, phrase: str, recipients: list[str] | None = None,
          deputy: str = "", evidence_paths: list[str] | None = None,
          reviewer: str = "", fix_author: str = "") -> dict:
    match = ORDER.fullmatch(phrase)
    if not match:
        raise ValueError("expected 集结/开火准备/停火/换防/复火 T<number>[:signature]")
    with _task_writer_lock(root, match.group(2)):
        return _issue_locked(root, actor=actor, phrase=phrase, recipients=recipients,
                             deputy=deputy, evidence_paths=evidence_paths,
                             reviewer=reviewer, fix_author=fix_author)


def _issue_locked(root: Path, *, actor: str, phrase: str, recipients: list[str] | None = None,
                  deputy: str = "", evidence_paths: list[str] | None = None,
                  reviewer: str = "", fix_author: str = "") -> dict:
    match = ORDER.fullmatch(phrase)
    if not match:
        raise ValueError("expected 集结/开火准备/停火/换防/复火 T<number>[:signature]")
    word, task, signature = match.groups()
    if not AGENT.fullmatch(actor) or any(not AGENT.fullmatch(who) for who in (recipients or [])):
        raise ValueError("invalid agent")
    if deputy and (not AGENT.fullmatch(deputy) or deputy == actor):
        raise ValueError("deputy must be another agent")
    evidence = evidence_paths or []
    if any(not item.strip() for item in evidence):
        raise ValueError("empty evidence reference")
    current = status(root, task)
    action = {"集结": "assemble", "开火准备": "prepare", "停火": "ceasefire", "换防": "handoff", "复火": "resume"}[word]
    if action == "assemble" and current["lead"] and current["lead"] != actor:
        raise ValueError("existing lead must hand off; assembly cannot seize leadership")
    if action in {"ceasefire", "resume"} and not signature:
        raise ValueError("ceasefire and resume require a failure signature")
    if action == "ceasefire" and not evidence:
        raise ValueError("ceasefire requires an evidence reference")
    if action == "ceasefire" and any(hold["signature"] == signature for hold in current["active_holds"]):
        raise ValueError("same failure signature already under active ceasefire")
    if action == "prepare" and current["active_holds"]:
        raise ValueError("task has active ceasefire; diagnose or resolve before preparing")
    if action == "handoff" and (not current["deputy"] or actor != current["lead"] or not evidence):
        raise ValueError("handoff requires current lead, named deputy and evidence handoff")
    if action == "resume":
        matching = [hold for hold in current["active_holds"] if hold["signature"] == signature]
        if (len(matching) != 1 or not evidence or reviewer != actor
                or not AGENT.fullmatch(fix_author) or fix_author == actor):
            raise ValueError("resume must be issued by the independent reviewer, name a different fix author and cite evidence")
    now = datetime.now(timezone.utc)
    event_id = now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    event = {"id": event_id, "at": now.isoformat(), "task": task, "action": action,
             "actor": actor, "phrase": phrase.strip(), "signature": signature or "",
             "evidence_paths": evidence, "deputy": deputy,
             "reviewer": reviewer, "fix_author": fix_author,
             "resolves": matching[0]["id"] if action == "resume" else "",
             "new_lead": current["deputy"] if action == "handoff" else "",
             "execution_requested": False, "upload_authorized": False}
    path = Path(root) / "battle_events" / task / f"{event_id}.json"
    _atomic_json(path, event)
    targets = set(recipients or [])
    targets.update(who for who in (current["lead"], current["deputy"], deputy) if who)
    targets.discard(actor)
    delivered = []
    for who in sorted(targets):
        message = {"id": event_id, "at": event["at"], "sender": actor, "to": who,
                   "topic": task, "kind": "warning" if action == "ceasefire" else "context",
                   "text": f"{event['phrase']}\n证据: {', '.join(evidence) or '(待补)'}\n事件: {path}\n请读证据后 ACK；投递不等于接单。"}
        target = Path(root) / "messages" / who / f"{event_id}.json"
        _atomic_json(target, message)
        delivered.append(who)
    return {"event": event, "deposited_to": delivered,
            "state": status(root, task), "note": "Mailbox deposit is not ACK, model wake or execution."}
