"""Deterministic baseline planner; model agents may refine, never erase evidence."""
from __future__ import annotations

import json
from pathlib import Path

from .workspace_identity import ensure_workspace_id


def plan(root: str | Path, mission_id: str) -> dict:
    p = Path(root) / "missions" / f"{mission_id}.json"
    if not p.exists(): raise FileNotFoundError(mission_id)
    m = ensure_workspace_id(json.loads(p.read_text(encoding="utf-8")))
    stages = [
        {"stage_id": "research", "state": "ready", "depends_on": [], "description": "collect facts, constraints and prior art"},
        {"stage_id": "implementation", "state": "blocked", "depends_on": ["research"], "description": "build the proposed solution"},
        {"stage_id": "validation", "state": "blocked", "depends_on": ["implementation"], "description": "run tests and verify evidence"},
        {"stage_id": "delivery", "state": "blocked", "depends_on": ["validation"], "description": "assemble artifacts and final report"},
    ]
    m["stages"] = stages
    m["planner"] = {"kind": "deterministic-baseline", "deliverables": m.get("deliverables", []), "constraints": m.get("constraints", [])}
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return m
