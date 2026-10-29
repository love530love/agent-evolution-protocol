"""Persistent stage transitions and checkpoints for missions."""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone


def _now(): return datetime.now(timezone.utc).isoformat()


def _path(root, mission_id): return Path(root) / "missions" / f"{mission_id}.json"


def _load(root, mission_id):
    p = _path(root, mission_id)
    if not p.exists(): raise FileNotFoundError(mission_id)
    return p, json.loads(p.read_text(encoding="utf-8"))


def transition(root, *, mission_id: str, stage_id: str, state: str, actor: str,
               evidence: list[str] | None = None) -> dict:
    p, mission = _load(root, mission_id)
    stage = next((s for s in mission["stages"] if s["stage_id"] == stage_id), None)
    if stage is None: raise ValueError("unknown stage")
    if state not in {"ready", "running", "blocked", "completed", "failed"}:
        raise ValueError("invalid stage state")
    for dep in stage.get("depends_on", []):
        prior = next(s for s in mission["stages"] if s["stage_id"] == dep)
        if state in {"running", "completed"} and prior.get("state") != "completed":
            raise ValueError(f"dependency not completed: {dep}")
    stage.update({"state": state, "actor": actor, "updated_at": _now()})
    if evidence is not None: stage["evidence"] = evidence
    mission["state"] = "completed" if all(s["state"] == "completed" for s in mission["stages"]) else "running"
    p.write_text(json.dumps(mission, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return mission


def checkpoint(root, *, mission_id: str, stage_id: str, actor: str, payload: dict) -> dict:
    p, mission = _load(root, mission_id)
    stage = next((s for s in mission["stages"] if s["stage_id"] == stage_id), None)
    if stage is None: raise ValueError("unknown stage")
    stage["checkpoint"] = {"actor": actor, "payload": payload, "at": _now()}
    p.write_text(json.dumps(mission, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return stage["checkpoint"]
