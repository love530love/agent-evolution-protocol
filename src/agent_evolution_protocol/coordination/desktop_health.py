"""Small circuit breaker for desktop discovery and transport health."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4


def _path(root: Path) -> Path:
    return Path(root) / "desktop_health.json"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def record(root: Path, *, component: str, status: str, error: str = "", threshold: int = 2) -> dict:
    """Record one bounded probe result and expose a deterministic circuit state."""
    if not component.strip() or not status.strip() or threshold < 1:
        raise ValueError("component, status and positive threshold are required")
    path = _path(root)
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        current = {}
    failures = int(current.get("consecutive_failures", 0)) if isinstance(current.get("consecutive_failures", 0), int) else 0
    failures = failures + 1 if status in {"TIMEOUT", "ERROR", "UNAVAILABLE"} else 0
    state = "DEGRADED" if failures >= threshold else "READY" if failures == 0 else "PROBING"
    value = {"component": component, "status": status, "circuit": state,
             "consecutive_failures": failures, "threshold": threshold,
             "error": error[:1200], "at": datetime.now(timezone.utc).isoformat()}
    _write(path, value)
    return value


def read(root: Path) -> dict:
    path = _path(root)
    if not path.is_file():
        return {"component": "desktop", "status": "UNKNOWN", "circuit": "UNKNOWN", "consecutive_failures": 0}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"component": "desktop", "status": "UNKNOWN", "circuit": "UNKNOWN", "consecutive_failures": 0}
    return value if isinstance(value, dict) else {"component": "desktop", "status": "UNKNOWN", "circuit": "UNKNOWN"}
