"""Explicit, expiring capability declarations; no presence inference or polling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from uuid import uuid4


SAFE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _names(items: list[str], label: str) -> list[str]:
    if not items or any(not SAFE_NAME.fullmatch(value) for value in items):
        raise ValueError(f"{label} needs nonempty lowercase names")
    return sorted(set(items))


def declare(root: Path, *, agent: str, chips: list[str], compilers: list[str],
            strengths: list[str], evidence: list[str], ttl_minutes: int) -> dict:
    if not SAFE_NAME.fullmatch(agent):
        raise ValueError("invalid agent")
    if not 1 <= ttl_minutes <= 120:
        raise ValueError("TTL must be 1..120 minutes")
    if not evidence or any(not value.strip() for value in evidence):
        raise ValueError("evidence references required")
    now = datetime.now(timezone.utc)
    payload = {
        "agent": agent, "chips": _names(chips, "chips"),
        "compilers": _names(compilers, "compilers"),
        "strengths": _names(strengths, "strengths"),
        "evidence": evidence,
        "declared_at": now.isoformat(),
        "available_until": (now + timedelta(minutes=ttl_minutes)).isoformat(),
        "note": "Self-declared availability is not an ACK or proof of current client control.",
    }
    path = Path(root) / "capabilities" / f"{agent}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return payload


def match(root: Path, *, chip: str = "", compiler: str = "", strength: str = "") -> dict:
    for value in (chip, compiler, strength):
        if value and not SAFE_NAME.fullmatch(value):
            raise ValueError("invalid capability filter")
    now = datetime.now(timezone.utc)
    candidates = []
    for path in sorted((Path(root) / "capabilities").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        try:
            expiry = datetime.fromisoformat(data["available_until"])
        except (KeyError, ValueError, TypeError):
            continue
        if chip and chip not in data.get("chips", []):
            continue
        if compiler and compiler not in data.get("compilers", []):
            continue
        if strength and strength not in data.get("strengths", []):
            continue
        candidates.append({"agent": data.get("agent"), "availability": "DECLARED_ACTIVE" if expiry > now else "EXPIRED",
                           "available_until": data["available_until"], "evidence": data.get("evidence", [])})
    return {"matches": candidates, "active": [item for item in candidates if item["availability"] == "DECLARED_ACTIVE"],
            "note": "Capability match does not wake or assign an agent; wait for explicit ACK before relying on them."}
