"""Offline commit-reveal ledger. No model calls, polling, or external execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
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


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(".agent-evolution"))
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
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.func(args)
        return 0
    except ProtocolError as exc:
        print(f"protocol error: {exc}", file=sys.stderr)
        return 2
