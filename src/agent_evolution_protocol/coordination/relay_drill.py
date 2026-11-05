"""One-shot orchestration for an in-turn multi-agent relay drill.

The operator's completion criterion (2026-11-05): online agents use this
project to dispatch work to *other* agents, return an evidence-backed summary,
and the run counts as finished only after every agent has taken its turn and
every loop is VERIFIED.

This module is deliberately small.  It does **not** wake desktop windows,
**not** report presence on an agent's behalf, **not** fabricate an ACK, and
**not** invent a verdict.  It:

* reads *fresh* presence evidence (``presence.effective``);
* forms a directed cycle so every agent sends once and receives once;
* on ``--apply`` only, calls the existing ``team_dispatch.create`` once per leg;
* recomputes each leg with ``loop_verdict.check`` and sets ``run_complete``
  only when all of them are ``VERIFIED``.

Presence is self-reported: each participating agent must run
``scripts/join-agent.ps1`` itself.  A coordinator that writes ``online`` for
its peers would be rehearsing with itself, which is exactly what this drill
must rule out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import bridge, loop_verdict, presence, team_dispatch

NOTE = ("One-shot, model-free relay drill. Presence is self-reported by each "
        "agent; no ACK and no VERIFIED is ever fabricated here. "
        "A dispatch is not acceptance; a plan is not a passed run.")
HOW_TO_SIGNAL = (
    "Each agent reports itself online: "
    "scripts/join-agent.ps1 -Agent <name> -SessionId <id> -TtlMinutes 30 ; "
    "then it writes a real analysis and runs handoff_worker --action accept "
    "with the same event id and the analysis SHA-256. "
    "The coordinator never signals presence for a peer.")
EVIDENCE = ["docs/coordination/EVIDENCE_BACKED_AGENT_HANDOFF.zh-CN.md"]
PLAN_NAME = Path("relay_drill") / "plan.json"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fresh_online(root, *, now=None) -> list[str]:
    """Only ``presence.effective(row) == 'online'`` counts, not the stored state."""
    moment = now or datetime.now(timezone.utc)
    folder = Path(root) / "presence" / "agents"
    names = []
    for path in sorted(folder.glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if presence.effective(row, moment) == "online" and presence.NAME.fullmatch(path.stem):
            names.append(path.stem)
    return names


def _provisional_id(sender: str, target: str, moment: datetime) -> str:
    material = f"relay|{sender}|{target}|{moment.isoformat()}"
    return ("D" + moment.strftime("%Y%m%dT%H%M%S%fZ") + "-"
            + sha256_text(material)[:12])


def build_legs(online: list[str], moment: datetime) -> list[dict]:
    """A directed cycle: agents[i] -> agents[i+1], last -> first."""
    legs = []
    count = len(online)
    for index, sender in enumerate(online):
        target = online[(index + 1) % count]
        event_id = _provisional_id(sender, target, moment)
        legs.append({
            "sender": sender, "target": target,
            "task": f"T-RELAY-{index + 1}", "event_id": event_id,
            "instruction": (
                f"协作演练 {event_id}：先用 join-agent.ps1 自报在线；"
                "针对本条任务写一份真实分析，用同一事件 ID 执行 "
                "handoff_worker --action accept，回执须含分析文件 SHA-256；"
                f"然后由你向下一位 Agent（{target} 之后的环节）继续派发。"
                f"事件 ID：{event_id}"),
            "evidence": list(EVIDENCE),
        })
    return legs


def _base(root, *, online, blocked="") -> dict:
    return {"root": str(Path(root)), "fresh_online": online, "blocked": blocked,
            "applied": False, "replayed": False, "legs": [], "verdicts": [],
            "verified_legs": 0, "run_complete": False, "writes_performed": 0,
            "how_to_signal": HOW_TO_SIGNAL,
            "models_loaded": 0, "processes_started": 0, "ui_touched": False,
            "deployment_performed": False, "system_tasks_registered": False,
            "note": NOTE}


def _fill_status(report: dict, root, bridge_root) -> dict:
    verdicts = []
    for leg in report["legs"]:
        if leg.get("dispatch_state") == "FAILED":
            verdicts.append({"event_id": leg["event_id"], "verdict": "NOT_VERIFIED",
                             "reasons": ["DISPATCH_FAILED"]})
            continue
        result = loop_verdict.check(root, bridge_root, leg["event_id"],
                                    sender=leg["sender"], target=leg["target"])
        verdicts.append({"event_id": leg["event_id"], "verdict": result["verdict"],
                         "reasons": result["reasons"]})
    report["verdicts"] = verdicts
    report["verified_legs"] = sum(row["verdict"] == "VERIFIED" for row in verdicts)
    report["run_complete"] = bool(verdicts) and all(
        row["verdict"] == "VERIFIED" for row in verdicts)
    return report


def _load_plan(root) -> list[dict]:
    path = Path(root) / PLAN_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    legs = payload.get("legs") if isinstance(payload, dict) else None
    return legs if isinstance(legs, list) and legs else []


def _save_plan(root, legs: list[dict]) -> None:
    path = Path(root) / PLAN_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"legs": legs}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def run(root, bridge_root, *, apply: bool = False, legs=None, now=None) -> dict:
    """Plan, optionally dispatch, and recompute the verdict of every leg.

    ``legs`` lets an operator re-check a previously applied plan without
    generating new event ids.  ``apply=True`` is the only path that writes.
    """
    root, bridge_root = Path(root), Path(bridge_root)
    moment = now or datetime.now(timezone.utc)
    online = fresh_online(root, now=moment)
    report = _base(root, online=online)

    if legs is not None:
        report["legs"] = list(legs)
        return _fill_status(report, root, bridge_root)

    if len(online) < 2:
        report["blocked"] = "NEED_AT_LEAST_TWO_ONLINE_AGENTS"
        return report

    saved = _load_plan(root)
    if saved and not apply:
        report["legs"] = saved
        return _fill_status(report, root, bridge_root)
    if saved and apply:
        # A second --apply must never mint new event ids: that would dispatch
        # the same legs twice.  Re-check the saved plan instead; --fresh-plan
        # is the only explicit way to start a new run.
        report.update(applied=True, replayed=True, legs=saved, writes_performed=0)
        return _fill_status(report, root, bridge_root)

    planned = build_legs(online, moment)
    if not apply:
        report["legs"] = planned
        return _fill_status(report, root, bridge_root)

    applied, writes = [], 0
    for leg in planned:
        row = dict(leg)
        try:
            created = team_dispatch.create(
                root, sender=leg["sender"], to=leg["target"], task=leg["task"],
                instruction=leg["instruction"], evidence=list(leg["evidence"]),
                reply_minutes=30)
            row["planned_event_id"] = leg["event_id"]
            row["event_id"] = created["id"]
            row["dispatch_state"] = "CREATED"
            writes += 1
        except (OSError, ValueError, TypeError) as exc:
            row["dispatch_state"] = "FAILED"
            row["error"] = f"{type(exc).__name__}: {exc}"
        applied.append(row)
    _save_plan(root, applied)
    report.update(applied=True, legs=applied, writes_performed=writes)
    return _fill_status(report, root, bridge_root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One-shot in-turn multi-agent relay drill")
    parser.add_argument("--coord-root", type=Path, default=Path.cwd() / "coordination")
    parser.add_argument("--bridge-root", type=Path, default=bridge.ROOT)
    parser.add_argument("--apply", action="store_true",
                        help="create one existing team_dispatch request per leg")
    parser.add_argument("--fresh-plan", action="store_true",
                        help="ignore a previously saved plan and plan again")
    args = parser.parse_args(argv)
    if args.fresh_plan:
        path = args.coord_root / PLAN_NAME
        if path.is_file():
            path.unlink()
    report = run(args.coord_root, args.bridge_root, apply=args.apply)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
