"""Portable capability planning for weak agents and optional local models.

This module deliberately plans, rather than executes, computer actions.  A model
may propose an intent, but only a registered provider with a matching capability
and an explicit lease may execute it through the existing adapters.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any
from .model_contract import validate_proposal, safe_fallback
from .capability_metrics import score

OPERATIONS = {"observe", "read", "locate", "type", "click", "keys", "select", "screenshot", "wait", "send"}
RISK = {"observe": "low", "read": "low", "locate": "low", "screenshot": "low", "wait": "low",
        "type": "medium", "keys": "medium", "select": "medium", "send": "high", "click": "high"}
ALLOWED_PROVIDERS = {"rules", "uia", "cdp", "mcp", "browser", "file"}


@dataclass(frozen=True)
class Capability:
    provider: str
    operations: frozenset[str]
    status: str = "declared"  # declared, probed, task-tested, disabled
    priority: int = 100
    requires: tuple[str, ...] = ()


def _cap(row: dict[str, Any]) -> Capability:
    if not isinstance(row, dict) or row.get("provider") not in ALLOWED_PROVIDERS:
        raise ValueError("provider must be one of rules/uia/cdp/mcp/browser/file")
    operations = row.get("operations", [])
    if not isinstance(operations, list) or not operations or any(x not in OPERATIONS for x in operations):
        raise ValueError("operations must be a non-empty list of known operations")
    status = row.get("status", "declared")
    if status not in {"declared", "probed", "task-tested", "disabled"}:
        raise ValueError("invalid capability status")
    priority = row.get("priority", 100)
    if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 1000:
        raise ValueError("priority must be 0..1000")
    requires = row.get("requires", [])
    if not isinstance(requires, list) or any(not isinstance(x, str) or not x for x in requires):
        raise ValueError("requires must be a string list")
    return Capability(row["provider"], frozenset(operations), status, priority, tuple(requires))


def load_capabilities(path: Path | None = None) -> list[Capability]:
    """Load a manifest, falling back to safe built-ins when absent."""
    if path is None:
        env = os.environ.get("AEP_CAPABILITY_MANIFEST")
        path = Path(env) if env else None
    if path is None or not path.exists():
        return [Capability("rules", frozenset(OPERATIONS), "task-tested", 0)]
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("capabilities") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) > 64:
        raise ValueError("manifest must contain at most 64 capabilities")
    return [_cap(row) for row in rows]


def plan(intent: str, operation: str, *, manifest: Path | None = None, allow_high_risk: bool = False,
         execution_mode: str = "gated", metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return an auditable, non-executing action plan."""
    if not isinstance(intent, str) or not intent.strip() or len(intent) > 16000:
        raise ValueError("intent must be 1..16000 characters")
    if operation not in OPERATIONS:
        raise ValueError("unknown operation")
    risk = RISK[operation]
    if execution_mode not in {"gated", "delegated-open"}:
        raise ValueError("execution_mode must be gated or delegated-open")
    open_mode = execution_mode == "delegated-open"
    if risk == "high" and not (allow_high_risk or open_mode):
        return {"status": "REQUIRES_EXPLICIT_LEASE", "operation": operation, "risk": risk,
                "intent": intent, "candidates": [], "execution_started": False, "execution_mode": execution_mode}
    candidates = [c for c in load_capabilities(manifest)
                  if c.status != "disabled" and operation in c.operations]
    candidates.sort(key=lambda c: (-(score(metrics[c.provider]) if metrics and c.provider in metrics else 0),
                                   0 if c.status == "task-tested" else 1 if c.status == "probed" else 2, c.priority, c.provider))
    rows = [{"provider": c.provider, "status": c.status, "priority": c.priority,
             "requires": list(c.requires), "risk": risk} for c in candidates]
    return {"status": "PLAN_ONLY" if rows else "NO_CAPABILITY", "operation": operation,
            "risk": risk, "intent": intent, "candidates": rows, "execution_started": False,
            "execution_mode": execution_mode, "delegated_authority": open_mode,
            "next": "acquire shared desktop-input lease, execute through provider adapter, then read back evidence" if rows else "register or probe a provider"}


def plan_proposal(proposal: Any, *, manifest: Path | None = None, allow_high_risk: bool = False,
                  min_confidence: float = 0.55, execution_mode: str = "gated") -> dict[str, Any]:
    """Validate a model proposal and route it through the normal planner."""
    try:
        value = validate_proposal(proposal)
    except ValueError as exc:
        return safe_fallback(proposal, reason=str(exc))
    if value["confidence"] < min_confidence:
        return {"status": "MODEL_CONFIDENCE_TOO_LOW", "execution_started": False,
                "confidence": value["confidence"], "threshold": min_confidence,
                "next": "ask a stronger agent or provide a structured intent"}
    result = plan(value["intent"], value["operation"], manifest=manifest, allow_high_risk=allow_high_risk,
                  execution_mode=execution_mode)
    result["model_confidence"] = value["confidence"]
    result["model_reason"] = value["reason"]
    return result
