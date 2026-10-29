"""Model-free supervisor decisions for persisted missions."""
from __future__ import annotations

from pathlib import Path
import json


def inspect(root: str | Path) -> dict:
    missions = []
    for p in sorted((Path(root) / "missions").glob("M-*.json")):
        try: missions.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError): continue
    actions = []
    for m in missions:
        state = m.get("state")
        if state == "completed":
            actions.append({"mission_id": m["mission_id"], "action": "report_delivery"})
            continue
        running = [s for s in m.get("stages", []) if s.get("state") == "running"]
        ready = [s for s in m.get("stages", []) if s.get("state") == "ready"]
        failed = [s for s in m.get("stages", []) if s.get("state") == "failed"]
        if failed:
            actions.append({"mission_id": m["mission_id"], "action": "recovery_review", "stages": [s["stage_id"] for s in failed]})
        elif running:
            actions.append({"mission_id": m["mission_id"], "action": "observe", "stages": [s["stage_id"] for s in running]})
        elif ready:
            actions.append({"mission_id": m["mission_id"], "action": "assign", "stages": [s["stage_id"] for s in ready]})
        else:
            actions.append({"mission_id": m["mission_id"], "action": "blocked_review"})
    return {"missions": len(missions), "actions": actions}
