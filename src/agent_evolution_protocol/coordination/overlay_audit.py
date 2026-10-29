"""Append-only audit trail for observed overlays and their verified actions."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from .overlay_detector import Overlay


def record(root: Path, overlay: Overlay, *, agent: str, action: str = "",
           verified: bool = False, event_id: str = "") -> dict:
    item = {
        "id": uuid4().hex,
        "event_id": event_id,
        "agent": agent,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "overlay": overlay.to_dict(),
        "action": action,
        "verified": bool(verified),
    }
    path = Path(root) / "overlay_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
    return item


def read(root: Path, *, agent: str | None = None, limit: int = 100) -> list[dict]:
    path = Path(root) / "overlay_audit.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        if not line.strip():
            continue
        item = json.loads(line)
        if agent is None or item.get("agent") == agent:
            rows.append(item)
    return rows
