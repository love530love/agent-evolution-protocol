"""Atomic JSON and canonical hashing shared by the coalition layer."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


def canonical(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(payload) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


def read_json(path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path, payload) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-",
                                             suffix=".json")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def coerce_utc(value, label="moment") -> datetime:
    """Parse a timestamp, attaching UTC to a naive value instead of guessing."""
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"INVALID_{str(label).upper()}") from exc
    else:
        raise ValueError(f"{label} must be an ISO timestamp or datetime")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def parse_utc_or_none(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return coerce_utc(value)
    except ValueError:
        return None
