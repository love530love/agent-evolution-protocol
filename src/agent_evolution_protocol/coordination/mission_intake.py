"""Small, deterministic mission intake contract for long-running work."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from .workspace_identity import ensure_workspace_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(root: Path, *, goal: str, workspace: str = "", deadline: str = "",
           budget: str = "", deliverables: list[str] | None = None,
           constraints: list[str] | None = None, workspace_id: str = "") -> dict:
    if not goal.strip():
        raise ValueError("goal is required")
    mission_id = "M-" + uuid4().hex[:12]
    mission = ensure_workspace_id({
        "mission_id": mission_id, "goal": goal.strip(), "workspace": workspace,
        "workspace_id": workspace_id,
        "deadline": deadline, "budget": budget, "deliverables": deliverables or [],
        "constraints": constraints or [], "state": "planned", "created_at": _now(),
        "stages": [{"stage_id": "intake", "state": "ready", "depends_on": [],
                    "description": "decompose goal into executable stages"},
                   {"stage_id": "execution", "state": "blocked", "depends_on": ["intake"],
                    "description": "execute assigned work and collect evidence"},
                   {"stage_id": "delivery", "state": "blocked", "depends_on": ["execution"],
                    "description": "verify and publish final artifacts"}],
    })
    p = Path(root) / "missions" / f"{mission_id}.json"; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(mission, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return mission


def load(root: Path, mission_id: str) -> dict:
    p = Path(root) / "missions" / f"{mission_id}.json"
    if not p.exists(): raise FileNotFoundError(mission_id)
    return json.loads(p.read_text(encoding="utf-8"))
