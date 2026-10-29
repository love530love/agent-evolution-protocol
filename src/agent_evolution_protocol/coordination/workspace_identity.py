"""Stable workspace identity and payload watermark helpers."""
from __future__ import annotations

import re
from uuid import uuid4
from typing import Any


WORKSPACE_ID = re.compile(r"^W-[0-9a-f]{16,32}$")


def new_workspace_id() -> str:
    return "W-" + uuid4().hex[:24]


def validate_workspace_id(value: str) -> str:
    if not isinstance(value, str) or not WORKSPACE_ID.fullmatch(value):
        raise ValueError("invalid workspace_id")
    return value


def ensure_workspace_id(record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with one valid workspace ID, preserving existing IDs."""
    if not isinstance(record, dict):
        raise ValueError("record must be an object")
    result = dict(record)
    current = result.get("workspace_id", "")
    result["workspace_id"] = validate_workspace_id(current) if current else new_workspace_id()
    return result


def watermark(payload: dict[str, Any], workspace_id: str) -> dict[str, Any]:
    """Attach an immutable workspace watermark; reject cross-workspace reuse."""
    validate_workspace_id(workspace_id)
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    existing = payload.get("workspace_id")
    if existing and existing != workspace_id:
        raise ValueError("WORKSPACE_MISMATCH")
    result = dict(payload)
    result["workspace_id"] = workspace_id
    return result
