"""Workspace-local, append-only conversation archive.

Only an explicitly connected hook or a manual publisher can provide content.
No client is scraped and no agent is woken by this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from uuid import uuid4


SAFE_AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
SAFE_EVENT = re.compile(r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$")


def archive_root(coord_root: Path) -> Path:
    return Path(coord_root) / "private_conversations"


def append_event(coord_root: Path, *, room: str, actor: str, source: str, kind: str,
                 text: str, session_id: str = "", cwd: str = "", raw_hook: dict | None = None) -> dict:
    root = archive_root(coord_root)
    events = root / "events"
    events.mkdir(parents=True, exist_ok=True)
    event_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:12]
    event = {
        "id": event_id, "recorded_at": datetime.now(timezone.utc).isoformat(),
        "room": room, "actor": actor, "source": source, "kind": kind,
        "text": text, "session_id": session_id, "cwd": cwd,
        "visibility": "workspace", "raw_hook": raw_hook,
    }
    path = events / f"{event_id}.json"
    temporary = events / f"{event_id}.{uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(event, handle, ensure_ascii=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return event


def cursor_for(coord_root: Path, agent: str) -> str:
    if not SAFE_AGENT.fullmatch(agent):
        raise ValueError("invalid agent")
    path = archive_root(coord_root) / "cursors" / f"{agent}.json"
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    value = str(data.get("through", ""))
    return value if SAFE_EVENT.fullmatch(value) else ""


def read_events(coord_root: Path, *, after: str = "", limit: int = 50,
                include_raw: bool = False) -> list[dict]:
    if after and not SAFE_EVENT.fullmatch(after):
        raise ValueError("invalid cursor")
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    folder = archive_root(coord_root) / "events"
    if not folder.exists():
        return []
    result = []
    for path in sorted(folder.glob("*.json")):
        if path.stem <= after or not SAFE_EVENT.fullmatch(path.stem):
            continue
        item = json.loads(path.read_text(encoding="utf-8"))
        if not include_raw:
            item.pop("raw_hook", None)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def acknowledge(coord_root: Path, *, agent: str, through: str) -> dict:
    if not SAFE_AGENT.fullmatch(agent) or not SAFE_EVENT.fullmatch(through):
        raise ValueError("invalid agent or event ID")
    root = archive_root(coord_root)
    if not (root / "events" / f"{through}.json").is_file():
        raise ValueError("unknown event ID")
    current = cursor_for(coord_root, agent)
    if current and through < current:
        raise ValueError("cursor cannot move backwards")
    path = root / "cursors" / f"{agent}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"agent": agent, "through": through, "acknowledged_at": datetime.now(timezone.utc).isoformat()}
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return payload
