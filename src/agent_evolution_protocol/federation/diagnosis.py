"""Fault classification and bounded recovery decisions.

Every blocker is mapped to one layer and one action before anything is retried.
The rules are deliberately small and total: an unknown signal becomes
``UNKNOWN`` and escalates instead of being guessed at, and a retryable fault
stops being retryable after a bounded number of attempts.
"""
from __future__ import annotations


LAYERS = ("agent", "resource", "external")
MAX_RETRY = 2
ACTIONS = ("retry", "cool_down", "switch_agent", "handoff", "quarantine",
           "arbitrate", "pause", "escalate")

RULES = {
    "token_exhausted": ("agent", "context_exhausted", "handoff", False),
    "no_heartbeat": ("agent", "stalled", "switch_agent", False),
    "refusal": ("agent", "refusal", "switch_agent", False),
    "loop": ("agent", "non_progress", "cool_down", True),
    "browser_blocked": ("resource", "risk_control", "cool_down", True),
    "captcha": ("resource", "human_challenge", "escalate", False),
    "network_error": ("resource", "transient", "retry", True),
    "foreign_message": ("external", "pollution", "quarantine", False),
    "conflict": ("external", "conclusion_conflict", "arbitrate", False),
    "manual_pause": ("external", "operator_pause", "pause", False),
}
PRIORITY = {action: index for index, action in enumerate(
    ("escalate", "pause", "handoff", "switch_agent", "arbitrate", "quarantine",
     "cool_down", "retry"))}


def classify(signal, *, attempts=0):
    """Map one signal to a layer and a bounded action."""
    if not isinstance(signal, str) or not signal.strip():
        raise ValueError("signal is required")
    if type(attempts) is not int or attempts < 0:
        raise ValueError("attempts must be a non-negative int")
    name = signal.strip()
    rule = RULES.get(name)
    if rule is None:
        return {"signal": name, "layer": "unknown", "category": "unclassified",
                "action": "escalate", "retryable": False, "attempts": attempts,
                "needs_human": True, "cooldown_seconds": 0,
                "reason": "unrecognised signal is escalated, never guessed"}
    layer, category, action, retryable = rule
    needs_human = action == "escalate"
    if retryable and attempts >= MAX_RETRY:
        return {"signal": name, "layer": layer, "category": category,
                "action": "escalate", "retryable": False, "attempts": attempts,
                "needs_human": True, "cooldown_seconds": 0,
                "reason": f"retry budget exhausted after {attempts} attempts"}
    cooldown = {"cool_down": 300, "switch_agent": 0, "handoff": 0,
                "quarantine": 0, "arbitrate": 0, "pause": 0,
                "retry": 30, "escalate": 0}[action]
    return {"signal": name, "layer": layer, "category": category, "action": action,
            "retryable": retryable, "attempts": attempts, "needs_human": needs_human,
            "cooldown_seconds": cooldown,
            "reason": f"{layer}/{category} handled by {action}"}


def decide(signals, *, attempts=None):
    """Collapse several signals into one action by fixed priority.

    ``signals`` may be names or ``(name, attempts)`` pairs. Ties keep the first
    occurrence, so the result is deterministic for the same input order.
    """
    if not isinstance(signals, (list, tuple)):
        raise ValueError("signals must be a list")
    attempts = attempts or {}
    rows = []
    for item in signals:
        if isinstance(item, str):
            rows.append(classify(item, attempts=attempts.get(item, 0)))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            rows.append(classify(item[0], attempts=item[1]))
        else:
            raise ValueError("each signal must be a name or (name, attempts)")
    if not rows:
        return {"action": "none", "signals": [], "needs_human": False,
                "escalate": False, "reason": "no signals"}
    rows.sort(key=lambda row: PRIORITY.get(row["action"], len(PRIORITY)))
    chosen = rows[0]
    return {"action": chosen["action"], "signal": chosen["signal"],
            "layer": chosen["layer"], "category": chosen["category"],
            "needs_human": any(row["needs_human"] for row in rows),
            "escalate": chosen["action"] == "escalate",
            "cooldown_seconds": chosen["cooldown_seconds"],
            "signals": rows, "reason": chosen["reason"]}
