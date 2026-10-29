"""Capability and health catalog for wake transports."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import json


@dataclass
class TransportRecord:
    name: str
    kind: str
    priority: int
    enabled: bool = True
    discovered: bool = False
    executable: bool = False
    last_status: str = "unknown"
    last_checked_at: str = ""
    failure_count: int = 0

    @property
    def score(self) -> int:
        if not self.enabled or not self.discovered:
            return -1
        return (1000 - self.priority * 10) + (100 if self.executable else 0) - min(self.failure_count * 25, 300)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(root: Path) -> Path:
    return Path(root) / "clients" / "transports.json"


def load(root: Path) -> list[TransportRecord]:
    p = _path(root)
    if not p.exists():
        return []
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
        return [TransportRecord(**row) for row in rows]
    except (OSError, ValueError, TypeError):
        return []


def upsert(root: Path, record: TransportRecord) -> TransportRecord:
    rows = [r for r in load(root) if r.name != record.name]
    record.last_checked_at = record.last_checked_at or _now()
    rows.append(record)
    p = _path(root); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(r) for r in rows], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
    return record


def observe(root: Path, *, name: str, status: str, executable: bool | None = None) -> TransportRecord | None:
    rows = load(root)
    found = next((r for r in rows if r.name == name), None)
    if found is None:
        return None
    found.last_status = status
    found.last_checked_at = _now()
    if executable is not None:
        found.executable = executable
    if status.upper() in {"FAILED", "OFFLINE", "TRANSPORT_ERROR"}:
        found.failure_count += 1
    elif status.upper() in {"DELIVERED", "ACKNOWLEDGED", "HEALTHY"}:
        found.failure_count = max(0, found.failure_count - 1)
    return upsert(root, found)


def ranked(root: Path) -> list[TransportRecord]:
    return sorted((r for r in load(root) if r.enabled and r.discovered),
                  key=lambda r: (-r.score, r.priority, r.name))
