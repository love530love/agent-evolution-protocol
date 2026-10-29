"""Bounded recovery decisions for wake delivery failures."""
from __future__ import annotations

RETRYABLE = {"TRANSPORT_ERROR", "OFFLINE", "ACK_TIMEOUT", "NETWORK_ERROR"}
NON_RETRYABLE = {"AMBIGUOUS_SESSION", "NO_SESSION", "AUTH_FAILED", "PERMISSION_DENIED"}


def decide(*, status: str, attempts: int, max_attempts: int = 2,
           side_effect_risk: str = "normal") -> dict:
    status = str(status).upper()
    if status in NON_RETRYABLE:
        return {"action": "BLOCK", "reason": status, "attempts": attempts}
    if status in RETRYABLE and attempts < max_attempts:
        return {"action": "FAILOVER", "reason": status, "attempts": attempts,
                "next_attempt": attempts + 1}
    if status in RETRYABLE and side_effect_risk == "high":
        return {"action": "ARBITRATE", "reason": status, "attempts": attempts}
    return {"action": "DEAD_LETTER", "reason": status, "attempts": attempts}
