"""Frozen task contracts for the Red Queen / Catfish / Creative Destruction workflow."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MODES = {"light", "standard", "assault"}
INTERACTIONS = {"guided", "checkpoint", "delegated"}
FIELDS = {"task", "owner", "objective", "acceptance", "constraints", "budget",
          "mode", "interaction", "authorizations", "stop_conditions", "restart_conditions"}
STAGES = ["contract", "independent_exploration", "freeze", "compare_claims",
          "discriminating_experiment", "integrate", "verify", "retrospect"]


def validate(request: dict) -> dict:
    if not isinstance(request, dict) or set(request) - FIELDS:
        raise ValueError("task contract must be an object with known fields")
    for field in ("task", "owner"):
        value = request.get(field)
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value) or value.upper() in {
            "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}:
            raise ValueError(f"invalid {field}")
    if not isinstance(request.get("objective"), str) or not request["objective"].strip():
        raise ValueError("objective is required")
    for field in ("acceptance", "stop_conditions", "restart_conditions", "constraints", "authorizations"):
        value = request.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError(f"{field} must be a list of nonempty strings")
        if field in {"acceptance", "stop_conditions"} and not value:
            raise ValueError(f"{field} is required")
    budget = request.get("budget")
    if not isinstance(budget, dict) or set(budget) != {"max_rounds", "max_minutes"}:
        raise ValueError("budget requires max_rounds and max_minutes")
    if isinstance(budget["max_rounds"], bool) or not isinstance(budget["max_rounds"], int) or budget["max_rounds"] < 1:
        raise ValueError("max_rounds must be a positive integer")
    minutes = budget["max_minutes"]
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not math.isfinite(minutes) or minutes <= 0:
        raise ValueError("max_minutes must be positive and finite")
    if request.get("mode") not in MODES or request.get("interaction") not in INTERACTIONS:
        raise ValueError("invalid mode or interaction")
    # Reject NaN and non-JSON data; round trip detaches caller-owned mutable data.
    return json.loads(json.dumps(request, ensure_ascii=False, allow_nan=False))


def create(root: Path, request: dict) -> dict:
    contract = validate(request)
    encoded = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload = {"schema": "aep-reasoning-contract-v1", "created_at": datetime.now(timezone.utc).isoformat(),
               "contract": contract, "sha256": hashlib.sha256(encoded).hexdigest()}
    folder = Path(root) / "reasoning_contracts" / contract["task"]
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "contract.json").open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return status(root, contract["task"])


def status(root: Path, task: str) -> dict:
    if not isinstance(task, str) or not IDENTIFIER.fullmatch(task):
        raise ValueError("invalid task")
    payload = json.loads((Path(root) / "reasoning_contracts" / task / "contract.json").read_text(encoding="utf-8"))
    contract = validate(payload["contract"])
    encoded = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if contract["task"] != task or hashlib.sha256(encoded).hexdigest() != payload.get("sha256"):
        raise ValueError("frozen contract changed")
    return {**payload, "stages": STAGES if contract["mode"] != "light" else ["contract", "execute", "verify", "retrospect"],
            "execution_started": False, "authorization_verified": False,
            "note": "Declared budgets and authorizations are planning data; execution, leases, ACK and independent verification use their existing gates."}
