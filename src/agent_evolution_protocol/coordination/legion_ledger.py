"""Legion contribution ledger: score, detect coasting, and issue challenges.

The Red Queen keeps agents running; the catfish keeps them honest. This module
is the scoreboard that makes both visible, so "polite yielding" and "silent
coasting" show up as negative evidence instead of being mistaken for harmony.

It never wakes, assigns, or uploads anything. It only reads and writes local
ledger files under the coordination root.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from uuid import uuid4


SAFE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
SAFE_TASK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Positive entries reward evidence that moves the legion forward. Negative
# entries price the behaviours that quietly pull a multi-agent system back to
# mediocrity: repeating a known trap, coasting, and yielding without evidence.
WEIGHTS = {
    "proposal": 1,
    "package": 2,
    "upload_evidence": 2,
    "unreconciled": -1,
    "cross_review": 3,
    "playbook": 3,
    "falsification": 4,
    "foreign_fix": 5,
    "breakthrough": 6,
    "silent_round": -2,
    "yield_without_evidence": -2,
    "repeat_mistake": -3,
    "blind_upload": -4,
}

EVIDENCE_REQUIRED = {"cross_review", "falsification", "foreign_fix", "breakthrough", "repeat_mistake", "blind_upload", "unreconciled"}
PRODUCTIVE = {name for name, weight in WEIGHTS.items() if weight > 0}


def _id(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"invalid {label}")
    return value


def _ledger(root: Path) -> Path:
    return Path(root) / "legion" / "events"


def _write_once(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _events(root: Path) -> list[dict]:
    folder = _ledger(root)
    if not folder.exists():
        return []
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json"))]


def record(root: Path, *, agent: str, kind: str, reporter: str, task: str = "",
           evidence: list[str] | None = None, note: str = "", signature: str = "") -> dict:
    _id(agent, SAFE_NAME, "agent")
    _id(reporter, SAFE_NAME, "reporter")
    if kind not in WEIGHTS:
        raise ValueError("unknown contribution kind: " + ", ".join(sorted(WEIGHTS)) + " required")
    if task:
        _id(task, SAFE_TASK, "task")
    refs = [item for item in (evidence or []) if item and item.strip()]
    if kind in EVIDENCE_REQUIRED and not refs:
        raise ValueError(f"{kind} requires at least one evidence reference")
    if not note.strip():
        raise ValueError("note is required: state what was learned or lost")
    now = datetime.now(timezone.utc)
    event_id = now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    payload = {
        "id": event_id, "at": now.isoformat(), "agent": agent, "kind": kind,
        "weight": WEIGHTS[kind], "reporter": reporter, "task": task.upper() if task else "",
        "signature": signature.strip(), "evidence": refs, "note": note.strip(),
    }
    _write_once(_ledger(root) / f"{event_id}.json", payload)
    return payload


def _since(root: Path, since_hours: float) -> list[dict]:
    if since_hours <= 0:
        return _events(root)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    kept = []
    for event in _events(root):
        try:
            stamp = datetime.fromisoformat(event["at"])
        except (KeyError, ValueError, TypeError):
            continue
        if stamp >= cutoff:
            kept.append(event)
    return kept


def scoreboard(root: Path, *, since_hours: float = 0) -> dict:
    events = _since(root, since_hours)
    scores: dict[str, int] = defaultdict(int)
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    last: dict[str, str] = {}
    tasks: dict[str, set[str]] = defaultdict(set)
    for event in events:
        agent = event.get("agent", "")
        if not agent:
            continue
        scores[agent] += int(event.get("weight", 0))
        counts[agent][event.get("kind", "")] += 1
        last[agent] = max(last.get(agent, ""), event.get("at", ""))
        if event.get("task"):
            tasks[agent].add(event["task"])
    rows = []
    for agent in sorted(scores, key=lambda name: (-scores[name], name)):
        positive = sum(count for kind, count in counts[agent].items() if WEIGHTS.get(kind, 0) > 0)
        negative = sum(count for kind, count in counts[agent].items() if WEIGHTS.get(kind, 0) < 0)
        rows.append({
            "agent": agent, "score": scores[agent],
            "productive_events": positive, "penalty_events": negative,
            "tasks": sorted(tasks[agent]), "last_event_at": last[agent],
            "kinds": dict(sorted(counts[agent].items())),
        })
    return {
        "window_hours": since_hours, "event_count": len(events), "rows": rows,
        "leader": rows[0]["agent"] if rows else "",
        "weights": WEIGHTS,
        "note": "Score is local bookkeeping only; it never authorises an upload or assigns work.",
    }


def slacking(root: Path, *, expected: list[str] | None = None, window_hours: float = 24,
             min_productive: int = 1) -> dict:
    events = _since(root, window_hours)
    per_agent: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        per_agent[event.get("agent", "")].append(event)
    roster = sorted({*(expected or []), *per_agent.keys()})
    findings = []
    for agent in roster:
        if agent and not SAFE_NAME.fullmatch(agent):
            raise ValueError("invalid agent in expected roster")
        items = per_agent.get(agent, [])
        productive = [item for item in items if item.get("kind") in PRODUCTIVE]
        repeat: dict[str, int] = defaultdict(int)
        for item in items:
            if item.get("kind") == "repeat_mistake" and item.get("signature"):
                repeat[item["signature"]] += 1
        score = sum(int(item.get("weight", 0)) for item in items)
        reasons = []
        if not items:
            reasons.append("no_local_evidence_in_window")
        elif not productive:
            reasons.append("no_productive_contribution")
        elif len(productive) < min_productive:
            reasons.append("below_expected_throughput")
        if score < 0:
            reasons.append("negative_net_score")
        for signature, count in sorted(repeat.items()):
            if count >= 2:
                reasons.append(f"repeat_trap:{signature}")
        if any(item.get("kind") == "yield_without_evidence" for item in items):
            reasons.append("yielded_without_evidence")
        if reasons:
            findings.append({"agent": agent, "score": score, "reasons": reasons,
                             "productive_events": len(productive), "penalty_events": len(items) - len(productive)})
    return {
        "window_hours": window_hours, "roster": roster, "findings": findings,
        "clean": [agent for agent in roster if agent not in {row["agent"] for row in findings}],
        "note": "A finding is a question to answer with evidence, not a punishment and not a reason to hand the task away.",
    }


def challenge(root: Path, *, agent: str, reason: str, alternative: str,
              predicted_signature: str, cheapest_test: str, stop_condition: str,
              evidence: list[str] | None = None, task: str = "") -> dict:
    """Turn a coasting finding into a bounded, falsifiable catfish challenge."""
    _id(agent, SAFE_NAME, "agent")
    for label, value in (("reason", reason), ("alternative", alternative),
                         ("predicted_signature", predicted_signature),
                         ("cheapest_test", cheapest_test), ("stop_condition", stop_condition)):
        if not value.strip():
            raise ValueError(f"{label} is required")
    if task:
        _id(task, SAFE_TASK, "task")
    now = datetime.now(timezone.utc)
    payload = {
        "id": now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8],
        "at": now.isoformat(), "target": agent, "task": task.upper() if task else "",
        "reason": reason.strip(), "alternative": alternative.strip(),
        "predicted_signature": predicted_signature.strip(),
        "cheapest_test": cheapest_test.strip(), "stop_condition": stop_condition.strip(),
        "evidence": [item for item in (evidence or []) if item and item.strip()],
        "kind": "catfish_challenge",
        "note": "The target answers with a cheap experiment, not with courtesy. Silence counts as a failed prediction.",
    }
    target = Path(root) / "legion" / "challenges"
    target.mkdir(parents=True, exist_ok=True)
    _write_once(target / f"{payload['id']}.json", payload)
    return payload


def chase(root: Path, *, since_hours: float = 24, min_productive: int = 1) -> dict:
    """Gap-to-leader report: what each trailing agent must still produce."""
    board = scoreboard(root, since_hours=since_hours)
    rows = board["rows"]
    if not rows:
        return {"leader": "", "chases": [], "note": "No ledger events in window; nobody has evidence yet."}
    leader_score = rows[0]["score"]
    chases = []
    for row in rows[1:]:
        gap = leader_score - row["score"]
        if gap <= 0:
            continue
        suggestion = []
        if row["productive_events"] < min_productive:
            suggestion.append("带证据的交叉评审或一次最便宜的证伪实验")
        if row["penalty_events"]:
            suggestion.append("先修复并重跑已失败的同一签名，避免重复踩坑计负")
        if not row["tasks"]:
            suggestion.append("认领一个赛题并交出可复核产物")
        chases.append({"agent": row["agent"], "gap": gap, "next_evidence": suggestion or ["把已有打法写成 playbook 供他人复用"]})
    return {"leader": rows[0]["agent"], "leader_score": leader_score, "chases": chases,
            "note": "Chasing is additive: several agents may attack the same problem with different evidence."}
