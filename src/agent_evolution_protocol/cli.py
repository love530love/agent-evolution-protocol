"""Offline commit-reveal ledger and local coordination CLI.

No model calls, polling, or external execution are performed by this module.
Coordination commands only read/write local files under ``--coord-root``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import shutil
from datetime import datetime, timezone
from pathlib import Path

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PROPOSAL_FIELDS = {"candidate_id", "cell_id", "hypothesis", "structural_difference", "prediction", "cheapest_falsification", "max_budget", "stop_condition", "artifact_sha256", "contaminated"}
RESULT_FIELDS = {"candidate_id", "evaluator", "artifact_sha256", "environment", "command", "outcome", "metrics", "evidence_paths", "budget_spent", "information_gain"}
CHALLENGE_FIELDS = {"challenged_assumption", "alternative_hypothesis", "structural_difference", "predicted_signature", "cheapest_falsification", "max_budget", "stop_condition"}
THREAT_FIELDS = {"source", "observed_at", "freshness_ttl", "confidence", "impact", "affected_assumptions", "cheapest_confirmation", "recommended_response", "response_deadline"}
DECISION_FIELDS = {"decision", "alternatives_considered", "evidence_used", "dissent_preserved", "budget_spent", "prediction_vs_result", "what_would_reverse_decision", "next_review_at"}
GENESIS_HASH = "0" * 64
AGENTS = ("codex", "workbuddy", "qoder", "codebuddy", "github_copilot", "hermes_desktop", "autoclaw")
MESSAGE_KINDS = ("update", "proposal", "question", "result", "warning", "context")
WAKE_BUDGETS = ("one-shot", "bounded", "manual")
SESSION_POLICIES = ("forbid-by-default", "allow-on-explicit-wake", "allow")
HANDOFF_STYLES = ("digest-first", "full-context", "minimal")
TASK_PHASES = ("todo", "claimed", "working", "waiting", "blocked", "needs-review", "ready", "done", "abandoned")


class ProtocolError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(data: object) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProtocolError("JSON object required")
    return data


def valid_id(value: str, label: str) -> str:
    if not SAFE_ID.fullmatch(value):
        raise ProtocolError(f"invalid {label}: {value!r}")
    return value


def valid_agent(value: str) -> str:
    if value not in AGENTS:
        raise ProtocolError("agent must be one of: " + ", ".join(AGENTS))
    return value


def valid_task(value: str) -> str:
    value = value.upper()
    if not re.fullmatch(r"T[0-9]{1,4}(-[A-Z0-9]{1,16})?", value):
        raise ProtocolError("task must look like T89 or T103-R6Q")
    return value


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_write_json(path: Path, data: dict) -> None:
    atomic_write(path, canonical(data))


def read_json(path: Path) -> dict:
    return load(path)


def coord_root(args: argparse.Namespace) -> Path:
    root = getattr(args, "coord_root", None)
    if root is None:
        root = Path(os.environ.get("AEP_COORD_ROOT", "coordination"))
    return Path(root)


def coord_file(root: Path, *parts: str) -> Path:
    return root.joinpath(*parts)


def session_state(root: Path, who: str) -> dict:
    who = valid_agent(who)
    path = coord_file(root, "sessions", f"{who}.json")
    if path.exists():
        return read_json(path)
    return {
        "agent": who,
        "mode": "sticky",
        "thread_id": "",
        "new_session_policy": "forbid-by-default",
        "handoff_style": "digest-first",
        "notes": "",
        "updated_at": "",
    }


def unread_messages(root: Path, who: str) -> list[dict]:
    valid_agent(who)
    folder = coord_file(root, "messages", who)
    if not folder.exists():
        return []
    items: list[dict] = []
    for path in sorted(folder.glob("*.json")):
        if coord_file(root, "acks", who, path.name).exists():
            continue
        try:
            items.append(read_json(path))
        except ProtocolError:
            continue
    return items


def wake_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + hashlib.sha256(os.urandom(16)).hexdigest()[:8]


def append(directory: Path, event: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    events = get_events(directory)
    sealed = dict(event)
    sealed["sequence"] = len(events) + 1
    sealed["previous_event_sha256"] = events[-1]["event_sha256"] if events else GENESIS_HASH
    sealed["event_sha256"] = hashlib.sha256(canonical(sealed)).hexdigest()
    with (directory / "events.jsonl").open("ab") as handle:
        handle.write(canonical(sealed))
        handle.flush()
        os.fsync(handle.fileno())


def get_round(root: Path, round_id: str) -> tuple[Path, dict]:
    directory = root / valid_id(round_id, "round id")
    config = directory / "round.json"
    if not config.is_file():
        raise ProtocolError(f"round does not exist: {round_id}")
    return directory, load(config)


def get_events(directory: Path) -> list[dict]:
    ledger = directory / "events.jsonl"
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()] if ledger.exists() else []


def validate_fields(card: dict, required: set[str], label: str) -> None:
    missing = sorted(required - card.keys())
    if missing:
        raise ProtocolError(f"{label} missing fields: {', '.join(missing)}")


def ensure_active(events: list[dict]) -> None:
    holds = sum(e.get("type") in {"HUMAN_HOLD", "INCIDENT_HOLD"} for e in events)
    resumes = sum(e.get("type") == "ROUND_RESUMED" for e in events)
    if holds > resumes:
        raise ProtocolError("round is on HOLD; explicit human-approved resume required")


def audit_events(events: list[dict]) -> list[str]:
    errors = []
    previous = GENESIS_HASH
    for sequence, event in enumerate(events, 1):
        if event.get("sequence") != sequence:
            errors.append(f"event {sequence}: invalid sequence")
        if event.get("previous_event_sha256") != previous:
            errors.append(f"event {sequence}: previous hash mismatch")
        expected = dict(event)
        actual = expected.pop("event_sha256", None)
        computed = hashlib.sha256(canonical(expected)).hexdigest()
        if actual != computed:
            errors.append(f"event {sequence}: event hash mismatch")
        previous = actual or ""
    return errors


def validate_proposal(card: dict, cells: list[str]) -> None:
    missing = sorted(PROPOSAL_FIELDS - card.keys())
    if missing:
        raise ProtocolError(f"proposal missing fields: {', '.join(missing)}")
    valid_id(str(card["candidate_id"]), "candidate id")
    if valid_id(str(card["cell_id"]), "cell id") not in cells:
        raise ProtocolError("unregistered cell")
    for key in ("hypothesis", "structural_difference", "prediction", "cheapest_falsification", "stop_condition"):
        if not isinstance(card[key], str) or not card[key].strip():
            raise ProtocolError(f"non-empty text required: {key}")
    if not isinstance(card["max_budget"], (int, float)) or card["max_budget"] <= 0:
        raise ProtocolError("max_budget must be positive")
    if card["artifact_sha256"] is not None and not SHA256.fullmatch(str(card["artifact_sha256"])):
        raise ProtocolError("invalid artifact_sha256")
    if not isinstance(card["contaminated"], bool):
        raise ProtocolError("contaminated must be boolean")


def init_round(args: argparse.Namespace) -> None:
    directory = args.root / valid_id(args.round_id, "round id")
    if directory.exists():
        raise ProtocolError("round already exists")
    cells = [valid_id(value, "cell id") for value in args.cells]
    if len(cells) < 2 or len(cells) != len(set(cells)):
        raise ProtocolError("at least two unique cells required")
    if args.budget <= 0 or not 0.15 <= args.reserve <= 0.20:
        raise ProtocolError("positive budget and reserve between 0.15 and 0.20 required")
    config = {"schema_version": 1, "round_id": args.round_id, "task": args.task, "leader": args.leader, "cells": cells, "total_budget": args.budget, "reserve_fraction": args.reserve, "mode": "SHADOW", "created_at": utc_now()}
    atomic_write(directory / "round.json", canonical(config))
    append(directory, {"type": "ROUND_INITIALIZED", "at": utc_now(), "actor": args.leader})
    print(json.dumps(config, indent=2))


def commit(args: argparse.Namespace) -> None:
    directory, config = get_round(args.root, args.round_id)
    card = load(args.card.resolve())
    validate_proposal(card, config["cells"])
    events = get_events(directory)
    ensure_active(events)
    if any(e.get("type") == "CANDIDATE_COMMITTED" and e.get("cell_id") == card["cell_id"] for e in events):
        raise ProtocolError("cell already committed")
    reserved = sum(float(e.get("budget_reserved", 0)) for e in events if e.get("type") == "CANDIDATE_COMMITTED")
    usable = float(config["total_budget"]) * (1 - float(config["reserve_fraction"]))
    if reserved + float(card["max_budget"]) > usable + 1e-9:
        raise ProtocolError("commit exceeds exploration budget; reserve protected")
    digest = hashlib.sha256(canonical(card)).hexdigest()
    append(directory, {"type": "CANDIDATE_COMMITTED", "at": utc_now(), "actor": card["cell_id"], "cell_id": card["cell_id"], "candidate_id": card["candidate_id"], "card_sha256": digest, "budget_reserved": card["max_budget"], "contaminated": card["contaminated"]})
    print(digest)


def reveal(args: argparse.Namespace) -> None:
    directory, config = get_round(args.root, args.round_id)
    card = load(args.card.resolve())
    validate_proposal(card, config["cells"])
    events = get_events(directory)
    ensure_active(events)
    commits = {e["cell_id"]: e for e in events if e.get("type") == "CANDIDATE_COMMITTED"}
    missing = sorted(set(config["cells"]) - commits.keys())
    if missing:
        raise ProtocolError(f"reveal locked; missing commits: {', '.join(missing)}")
    digest = hashlib.sha256(canonical(card)).hexdigest()
    sealed = commits.get(card["cell_id"])
    if sealed is None or sealed["candidate_id"] != card["candidate_id"] or sealed["card_sha256"] != digest:
        raise ProtocolError("card does not match commitment")
    target = directory / "revealed" / f"{card['candidate_id']}.json"
    if target.exists():
        raise ProtocolError("candidate already revealed")
    atomic_write(target, canonical(card))
    append(directory, {"type": "CANDIDATE_REVEALED", "at": utc_now(), "actor": card["cell_id"], "cell_id": card["cell_id"], "candidate_id": card["candidate_id"], "card_sha256": digest})


def record_result(args: argparse.Namespace) -> None:
    directory, config = get_round(args.root, args.round_id)
    result = load(args.result.resolve())
    validate_fields(result, RESULT_FIELDS, "result")
    if not SHA256.fullmatch(str(result["artifact_sha256"])):
        raise ProtocolError("invalid artifact_sha256")
    events = get_events(directory)
    ensure_active(events)
    if not any(e.get("type") == "CANDIDATE_REVEALED" and e.get("candidate_id") == result["candidate_id"] for e in events):
        raise ProtocolError("candidate has not been revealed")
    card = load(directory / "revealed" / f"{result['candidate_id']}.json")
    if card["artifact_sha256"] is not None and card["artifact_sha256"] != result["artifact_sha256"]:
        raise ProtocolError("result artifact SHA mismatch")
    if not isinstance(result["budget_spent"], (int, float)) or result["budget_spent"] < 0:
        raise ProtocolError("budget_spent must be non-negative")
    if not isinstance(result["information_gain"], bool):
        raise ProtocolError("information_gain must be boolean")
    commitment = next(e for e in events if e.get("type") == "CANDIDATE_COMMITTED" and e.get("candidate_id") == result["candidate_id"])
    candidate_spent = sum(float(e.get("budget_spent", 0)) for e in events if e.get("type") == "RESULT_RECORDED" and e.get("candidate_id") == result["candidate_id"])
    total_spent = sum(float(e.get("budget_spent", 0)) for e in events if e.get("type") == "RESULT_RECORDED")
    if candidate_spent + float(result["budget_spent"]) > float(commitment["budget_reserved"]) + 1e-9:
        raise ProtocolError("result exceeds candidate committed budget")
    if total_spent + float(result["budget_spent"]) > float(config["total_budget"]) + 1e-9:
        raise ProtocolError("result exceeds round budget")
    digest = hashlib.sha256(canonical(result)).hexdigest()
    target = directory / "results" / f"{result['candidate_id']}-{digest[:12]}.json"
    atomic_write(target, canonical(result))
    append(directory, {"type": "RESULT_RECORDED", "at": utc_now(), "actor": result["evaluator"], "candidate_id": result["candidate_id"], "artifact_sha256": result["artifact_sha256"], "result_sha256": digest, "outcome": result["outcome"], "budget_spent": result["budget_spent"], "information_gain": result["information_gain"]})


def status(args: argparse.Namespace) -> None:
    directory, config = get_round(args.root, args.round_id)
    events = get_events(directory)
    committed = {e["cell_id"] for e in events if e.get("type") == "CANDIDATE_COMMITTED"}
    revealed = {e["cell_id"] for e in events if e.get("type") == "CANDIDATE_REVEALED"}
    held = sum(e.get("type") in {"HUMAN_HOLD", "INCIDENT_HOLD"} for e in events) > sum(e.get("type") == "ROUND_RESUMED" for e in events)
    print(json.dumps({"round_id": config["round_id"], "mode": "SHADOW", "cells": config["cells"], "committed": sorted(committed), "missing_commits": sorted(set(config["cells"]) - committed), "revealed": sorted(revealed), "held": held, "event_count": len(events), "ledger_valid": not audit_events(events), "automatic_execution": False}, indent=2))


def governance_event(args: argparse.Namespace) -> None:
    directory, config = get_round(args.root, args.round_id)
    card = load(args.file.resolve())
    required = {"challenge": CHALLENGE_FIELDS, "threat": THREAT_FIELDS, "decide": DECISION_FIELDS}[args.command]
    validate_fields(card, required, args.command)
    if args.command == "challenge" and (not isinstance(card["max_budget"], (int, float)) or card["max_budget"] <= 0):
        raise ProtocolError("challenge max_budget must be positive")
    if args.command == "threat":
        if card["confidence"] not in {"verified", "inferred", "rumor"} or card["impact"] not in {"low", "medium", "high", "critical"}:
            raise ProtocolError("invalid threat confidence or impact")
    event_type = {"challenge": "CHALLENGE_REQUEST", "threat": "EXTERNAL_THREAT", "decide": "LEADER_DECISION"}[args.command]
    append(directory, {"type": event_type, "at": utc_now(), "actor": args.actor, "round_id": config["round_id"], "card": card})


def hold(args: argparse.Namespace) -> None:
    directory, _ = get_round(args.root, args.round_id)
    events = get_events(directory)
    ensure_active(events)
    append(directory, {"type": args.kind, "at": utc_now(), "actor": args.actor, "reason": args.reason})


def resume(args: argparse.Namespace) -> None:
    directory, _ = get_round(args.root, args.round_id)
    events = get_events(directory)
    try:
        ensure_active(events)
    except ProtocolError:
        pass
    else:
        raise ProtocolError("round is not on HOLD")
    if not args.human_approved:
        raise ProtocolError("resume requires --human-approved")
    append(directory, {"type": "ROUND_RESUMED", "at": utc_now(), "actor": args.actor, "reason": args.reason, "human_approved": True})


def audit(args: argparse.Namespace) -> None:
    directory, _ = get_round(args.root, args.round_id)
    events = get_events(directory)
    errors = audit_events(events)
    report = {"round_id": args.round_id, "event_count": len(events), "valid": not errors, "errors": errors, "head_sha256": events[-1]["event_sha256"] if events else None}
    print(json.dumps(report, indent=2))
    if errors:
        raise ProtocolError("ledger audit failed")


def coord_doctor(args: argparse.Namespace) -> None:
    root = coord_root(args)
    checks = []
    checks.append({"name": "python", "ok": sys.version_info >= (3, 10), "detail": sys.version.split()[0]})
    checks.append({"name": "coord_root_exists", "ok": root.exists(), "detail": str(root)})
    required_dirs = ("messages", "acks", "sessions", "wake_queue", "wake_done", "wake_failed", "task_states")
    for name in required_dirs:
        path = coord_file(root, name)
        checks.append({"name": f"dir:{name}", "ok": path.exists() and path.is_dir(), "detail": str(path)})
    sessions = {who: session_state(root, who) for who in AGENTS}
    missing_thread = [
        who for who, state in sessions.items()
        if state.get("mode") == "sticky"
        and state.get("new_session_policy") != "allow"
        and not state.get("thread_id")
    ]
    queue_count = len(list(coord_file(root, "wake_queue").glob("*.json"))) if coord_file(root, "wake_queue").exists() else 0
    checks.append({"name": "sticky_sessions_locatable", "ok": not missing_thread, "detail": {"missing_thread_id": missing_thread}})
    checks.append({"name": "wake_queue_count", "ok": True, "detail": queue_count})
    ok = all(item["ok"] for item in checks if item["name"] != "sticky_sessions_locatable")
    report = {
        "coord_root": str(root),
        "ok": ok,
        "checks": checks,
        "guidance": [
            "Ordinary messages should not wake a model.",
            "Use digest-first handoff before any new session.",
            "A missing sticky thread_id means write a digest/status update instead of spawning.",
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not ok:
        raise ProtocolError("coordination doctor found missing required directories")


def coord_bootstrap(args: argparse.Namespace) -> None:
    root = coord_root(args)
    for name in ("messages", "acks", "sessions", "wake_queue", "wake_done", "wake_failed", "wake_pending", "wake_ack", "task_states", "claims"):
        coord_file(root, name).mkdir(parents=True, exist_ok=True)
    for who in AGENTS:
        path = coord_file(root, "sessions", f"{who}.json")
        if not path.exists():
            atomic_write_json(path, session_state(root, who))
    print(json.dumps({"coord_root": str(root), "created": True}, ensure_ascii=False, indent=2))


def coord_status(args: argparse.Namespace) -> None:
    root = coord_root(args)
    sessions = {who: session_state(root, who) for who in AGENTS}
    unread = {who: len(unread_messages(root, who)) for who in AGENTS}
    wake_dirs = {}
    for name in ("wake_queue", "wake_done", "wake_failed", "wake_pending"):
        path = coord_file(root, name)
        wake_dirs[name] = len(list(path.glob("*.json"))) if path.exists() else 0
    tasks = []
    task_dir = coord_file(root, "task_states")
    if task_dir.exists():
        for path in sorted(task_dir.glob("*.json")):
            try:
                tasks.append(read_json(path))
            except ProtocolError:
                pass
    print(json.dumps({"coord_root": str(root), "sessions": sessions, "unread": unread, "wake": wake_dirs, "tasks": tasks}, ensure_ascii=False, indent=2))


def coord_digest(args: argparse.Namespace) -> None:
    root = coord_root(args)
    who = valid_agent(args.agent)
    grouped: dict[str, dict] = {}
    for msg in unread_messages(root, who):
        topic = msg.get("topic", "(none)")
        entry = grouped.setdefault(topic, {"topic": topic, "count": 0, "latest_at": "", "senders": set(), "samples": []})
        entry["count"] += 1
        entry["latest_at"] = max(entry["latest_at"], msg.get("at", ""))
        entry["senders"].add(msg.get("sender", ""))
        if len(entry["samples"]) < args.samples:
            text = (msg.get("text", "") or "").replace("\r", " ").replace("\n", " ")
            entry["samples"].append({"id": msg.get("id"), "kind": msg.get("kind"), "preview": text[:args.preview_chars]})
    topics = []
    for entry in grouped.values():
        entry["senders"] = sorted(s for s in entry["senders"] if s)
        topics.append(entry)
    topics.sort(key=lambda item: item["latest_at"], reverse=True)
    print(json.dumps({"agent": who, "unread_count": len(unread_messages(root, who)), "topics": topics[:args.limit], "session": session_state(root, who)}, ensure_ascii=False, indent=2))


def coord_send(args: argparse.Namespace) -> None:
    root = coord_root(args)
    sender, receiver = valid_agent(args.sender), valid_agent(args.to)
    if sender == receiver:
        raise ProtocolError("sender and receiver must differ")
    text = args.text if args.text is not None else Path(args.text_file).read_text(encoding="utf-8")
    if not text.strip():
        raise ProtocolError("message body is empty")
    mid = wake_id()
    msg = {"id": mid, "at": utc_now(), "sender": sender, "to": receiver, "topic": args.topic, "kind": args.kind, "text": text}
    atomic_write_json(coord_file(root, "messages", receiver, f"{mid}.json"), msg)
    print(json.dumps(msg, ensure_ascii=False, indent=2))


def coord_ack_topic(args: argparse.Namespace) -> None:
    root = coord_root(args)
    who = valid_agent(args.agent)
    count = 0
    for msg in unread_messages(root, who):
        if msg.get("topic") != args.topic:
            continue
        mid = msg.get("id")
        if not mid:
            continue
        atomic_write_json(coord_file(root, "acks", who, f"{mid}.json"), {"id": mid, "agent": who, "at": utc_now(), "topic": args.topic, "batch": True})
        count += 1
    print(json.dumps({"agent": who, "topic": args.topic, "acked": count}, ensure_ascii=False, indent=2))


def coord_session_set(args: argparse.Namespace) -> None:
    root = coord_root(args)
    who = valid_agent(args.agent)
    state = {
        "agent": who,
        "mode": args.mode,
        "thread_id": args.thread_id or "",
        "new_session_policy": args.new_session_policy,
        "handoff_style": args.handoff_style,
        "notes": args.notes or "",
        "updated_at": utc_now(),
    }
    atomic_write_json(coord_file(root, "sessions", f"{who}.json"), state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def coord_wake(args: argparse.Namespace) -> None:
    root = coord_root(args)
    sender, receiver = valid_agent(args.sender), valid_agent(args.to)
    if sender == receiver:
        raise ProtocolError("sender and receiver must differ")
    task_name = valid_task(args.task)
    if not args.reason.strip():
        raise ProtocolError("wake reason is empty")
    wid = wake_id()
    event = {
        "id": wid,
        "type": "wake_request",
        "at": utc_now(),
        "sender": sender,
        "to": receiver,
        "task": task_name,
        "reason": args.reason,
        "budget": args.budget,
        "entrypoint": args.entrypoint or "",
        "requires_user_auth": bool(args.requires_user_auth),
        "no_polling": True,
        "dry_run": bool(args.dry_run),
        "session": session_state(root, receiver),
        "allow_new_session": bool(args.allow_new_session),
        "claim_timeout_seconds": args.claim_timeout_seconds,
    }
    atomic_write_json(coord_file(root, "wake_queue", f"{wid}.json"), event)
    print(json.dumps(event, ensure_ascii=False, indent=2))


def coord_wake_status(args: argparse.Namespace) -> None:
    root = coord_root(args)
    def count(name: str) -> int:
        path = coord_file(root, name)
        return len(list(path.glob("*.json"))) if path.exists() else 0
    log_path = coord_file(root, "wake_log.jsonl")
    tail = []
    if log_path.exists():
        tail = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()[-args.tail:] if line.strip()]
    print(json.dumps({"queue": count("wake_queue"), "done": count("wake_done"), "failed": count("wake_failed"), "pending": count("wake_pending"), "log_tail": tail}, ensure_ascii=False, indent=2))


def coord_archive_stale(args: argparse.Namespace) -> None:
    root = coord_root(args)
    queue = coord_file(root, "wake_queue")
    archive = coord_file(root, "wake_done", args.archive_name)
    if not queue.exists():
        print(json.dumps({"archived": 0, "archive": str(archive)}, ensure_ascii=False, indent=2))
        return
    archive.mkdir(parents=True, exist_ok=True)
    moved = []
    for path in sorted(queue.glob("*.json")):
        event = read_json(path)
        if not args.all:
            at = event.get("at", "")
            try:
                age_hours = (datetime.now(timezone.utc) - datetime.fromisoformat(at.replace("Z", "+00:00"))).total_seconds() / 3600
            except ValueError:
                age_hours = args.older_than_hours + 1
            if age_hours < args.older_than_hours:
                continue
        target = archive / path.name
        shutil.move(str(path), str(target))
        moved.append({"id": event.get("id", path.stem), "to": event.get("to"), "task": event.get("task")})
    digest = {
        "at": utc_now(),
        "archive": str(archive),
        "archived": len(moved),
        "items": moved,
        "note": "Archived wake requests are preserved as JSON; archive does not imply execution.",
    }
    atomic_write_json(archive / "ARCHIVE_DIGEST.json", digest)
    print(json.dumps(digest, ensure_ascii=False, indent=2))


def coord_onboarding(args: argparse.Namespace) -> None:
    root = coord_root(args)
    who = valid_agent(args.agent)
    payload = {
        "agent": who,
        "coord_root": str(root),
        "session": session_state(root, who),
        "unread_digest_command": f"agent-evolution --coord-root {root} coord-digest --agent {who}",
        "status_command": f"agent-evolution --coord-root {root} coord-status",
        "rules": [
            "Read README / introduction / latest status board before acting.",
            "Ordinary messages do not wake models; use explicit wake requests.",
            "Prefer digest-first handoff and existing sticky sessions.",
            "Do not create a new session unless the user explicitly authorizes it or allow_new_session=true.",
            "If the sticky session cannot be located, write a digest/status update and stop.",
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(".agent-evolution"))
    parser.add_argument("--coord-root", type=Path, default=Path(os.environ.get("AEP_COORD_ROOT", "coordination")), help="local coordination root for coord-* commands")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--round-id", required=True); init.add_argument("--task", required=True); init.add_argument("--leader", required=True); init.add_argument("--cells", nargs="+", required=True); init.add_argument("--budget", type=float, required=True); init.add_argument("--reserve", type=float, default=0.20); init.set_defaults(func=init_round)
    for name, func, option in (("commit", commit, "card"), ("reveal", reveal, "card"), ("record-result", record_result, "result")):
        command = commands.add_parser(name); command.add_argument("--round-id", required=True); command.add_argument(f"--{option}", type=Path, required=True); command.set_defaults(func=func)
    state = commands.add_parser("status"); state.add_argument("--round-id", required=True); state.set_defaults(func=status)
    for name in ("challenge", "threat", "decide"):
        command = commands.add_parser(name); command.add_argument("--round-id", required=True); command.add_argument("--actor", required=True); command.add_argument("--file", type=Path, required=True); command.set_defaults(func=governance_event)
    pause = commands.add_parser("hold"); pause.add_argument("--round-id", required=True); pause.add_argument("--actor", required=True); pause.add_argument("--reason", required=True); pause.add_argument("--kind", choices=["HUMAN_HOLD", "INCIDENT_HOLD"], default="HUMAN_HOLD"); pause.set_defaults(func=hold)
    restart = commands.add_parser("resume"); restart.add_argument("--round-id", required=True); restart.add_argument("--actor", required=True); restart.add_argument("--reason", required=True); restart.add_argument("--human-approved", action="store_true"); restart.set_defaults(func=resume)
    check = commands.add_parser("audit"); check.add_argument("--round-id", required=True); check.set_defaults(func=audit)
    commands.add_parser("doctor").set_defaults(func=coord_doctor)
    commands.add_parser("coord-bootstrap").set_defaults(func=coord_bootstrap)
    commands.add_parser("coord-status").set_defaults(func=coord_status)
    digest = commands.add_parser("coord-digest"); digest.add_argument("--agent", required=True); digest.add_argument("--limit", type=int, default=20); digest.add_argument("--samples", type=int, default=2); digest.add_argument("--preview-chars", type=int, default=240); digest.set_defaults(func=coord_digest)
    send = commands.add_parser("coord-send")
    send.add_argument("--from", dest="sender", required=True); send.add_argument("--to", required=True); send.add_argument("--topic", required=True); send.add_argument("--kind", choices=MESSAGE_KINDS, required=True)
    body = send.add_mutually_exclusive_group(required=True); body.add_argument("--text"); body.add_argument("--text-file")
    send.set_defaults(func=coord_send)
    ack_topic = commands.add_parser("coord-ack-topic"); ack_topic.add_argument("--agent", required=True); ack_topic.add_argument("--topic", required=True); ack_topic.set_defaults(func=coord_ack_topic)
    session = commands.add_parser("coord-session-set"); session.add_argument("--agent", required=True); session.add_argument("--mode", choices=("sticky", "manual", "spawn-allowed"), default="sticky"); session.add_argument("--thread-id"); session.add_argument("--new-session-policy", choices=SESSION_POLICIES, default="forbid-by-default"); session.add_argument("--handoff-style", choices=HANDOFF_STYLES, default="digest-first"); session.add_argument("--notes"); session.set_defaults(func=coord_session_set)
    wake = commands.add_parser("coord-wake"); wake.add_argument("--from", dest="sender", required=True); wake.add_argument("--to", required=True); wake.add_argument("--task", required=True); wake.add_argument("--reason", required=True); wake.add_argument("--budget", choices=WAKE_BUDGETS, default="one-shot"); wake.add_argument("--entrypoint"); wake.add_argument("--requires-user-auth", action="store_true"); wake.add_argument("--dry-run", action="store_true"); wake.add_argument("--allow-new-session", action="store_true"); wake.add_argument("--claim-timeout-seconds", type=int, default=600); wake.set_defaults(func=coord_wake)
    wake_status = commands.add_parser("coord-wake-status"); wake_status.add_argument("--tail", type=int, default=10); wake_status.set_defaults(func=coord_wake_status)
    archive = commands.add_parser("coord-archive-stale"); archive.add_argument("--older-than-hours", type=float, default=24); archive.add_argument("--archive-name", default="archived-stale"); archive.add_argument("--all", action="store_true"); archive.set_defaults(func=coord_archive_stale)
    onboarding = commands.add_parser("coord-onboarding"); onboarding.add_argument("--agent", required=True); onboarding.set_defaults(func=coord_onboarding)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.func(args)
        return 0
    except ProtocolError as exc:
        print(f"protocol error: {exc}", file=sys.stderr)
        return 2
