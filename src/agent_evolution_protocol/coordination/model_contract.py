"""Strict provider-neutral contract for optional model proposals."""
from __future__ import annotations
import json
from typing import Any

OPERATIONS = {"observe", "read", "locate", "type", "click", "keys", "select", "screenshot", "wait", "send"}
REQUIRED = {"intent", "operation", "confidence", "reason"}

def validate_proposal(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try: value = json.loads(value)
        except json.JSONDecodeError as exc: raise ValueError("model output must be JSON") from exc
    if not isinstance(value, dict) or not REQUIRED.issubset(value): raise ValueError("proposal requires intent, operation, confidence and reason")
    if set(value) - (REQUIRED | {"target", "constraints", "fallback"}): raise ValueError("proposal contains unknown fields")
    if not isinstance(value["intent"], str) or not 1 <= len(value["intent"]) <= 16000: raise ValueError("intent must be 1..16000 characters")
    if value["operation"] not in OPERATIONS: raise ValueError("unknown operation")
    confidence = value["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1: raise ValueError("confidence must be 0..1")
    if not isinstance(value["reason"], str) or not value["reason"].strip(): raise ValueError("reason must be non-empty")
    for field in ("target", "constraints", "fallback"):
        if field in value and not isinstance(value[field], (str, list, dict)): raise ValueError(f"{field} must be text, list or object")
    return {k: value[k] for k in value if k in (REQUIRED | {"target", "constraints", "fallback"})} | {"confidence": float(confidence)}

def safe_fallback(raw: Any, *, reason: str = "invalid model proposal") -> dict[str, Any]:
    return {"status": "MODEL_OUTPUT_REJECTED", "execution_started": False, "reason": reason, "next": "use aep route and coord-capability-plan", "raw_type": type(raw).__name__}
