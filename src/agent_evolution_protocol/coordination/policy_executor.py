"""Run a proposed action stream through AEP's guards, never around them.

A computer-use policy proposes; it does not get to decide whether the step
worked.  This executor is the only thing that touches the desktop, and it
enforces four rules that a model cannot be trusted to enforce on itself:

1. **The action vocabulary is AEP's, not the model's.** The allowlist is
   ``desktop_providers.OPERATIONS`` -- the same names the MCP bindings already
   use.  A model primitive that has no operation behind it is *refused*, not
   silently degraded: turning a double click into a single click is a wrong
   action that looks like a successful one.
2. **No shell.** A policy that emits ``bash`` gets a refusal and no execution.
   The training recipe's bash channel is a guest-side convenience; on a live
   host it is a privilege escalation with a friendly name.
3. **Coordinates are range-checked before conversion.** The model answers on a
   0-999 grid; a value outside it means it answered a different question, and
   clamping would turn that into a confident click somewhere arbitrary.
4. **An unverified step stops the run.** There is no "continue and hope": the
   verifier is the evidence ladder, and a step it cannot confirm ends the
   attempt so the caller can defer or fall back.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agent_evolution_protocol.coordination import desktop_providers, vision_grounding

COORDINATE_SCALE = vision_grounding.COORDINATE_SCALE

# Mage primitive -> AEP operation.  Only the ones with a real binding.
SUPPORTED = {
    "click": "click",
    "type": "type",
    "keypress": "keys",
    "wait": "wait",
    "screenshot": "screenshot",
}

# Mage primitives with no operation behind them today.  The reason is the point:
# it is the difference between "not implemented" and "would do the wrong thing".
UNSUPPORTED = {
    "double_click": "no click-count in the operation vocabulary; a double click would become a single click",
    "triple_click": "no click-count in the operation vocabulary; a triple click would become a single click",
    "move": "hover is not an operation, and a click implies a move",
    "drag": "no drag operation exists in the current bindings",
    "scroll": "no scroll operation exists in the current bindings",
}

# Anything shell-shaped.  Refused by name, before any argument is inspected.
FORBIDDEN = {"bash", "shell", "exec", "execute", "run", "run_command", "python", "terminal", "subprocess"}

CONTROL = {"terminate": "policy-terminated"}


class PolicyRefused(RuntimeError):
    """The policy asked for something that must never reach the desktop."""


@dataclass
class UnsupportedAction(PolicyRefused):
    action: str
    reason: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.action}: {self.reason}"


@dataclass
class Step:
    index: int
    action: str
    operation: str
    arguments: dict[str, Any]
    result: dict[str, Any] = field(default_factory=dict)
    verified: bool | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "action": self.action, "operation": self.operation,
                "arguments": self.arguments, "result": self.result,
                "verified": self.verified, "note": self.note}


def normalise(action: Any, *, window: vision_grounding.Region) -> dict[str, Any]:
    """Turn one model proposal into one executor instruction, or refuse it."""
    if not isinstance(action, dict):
        raise PolicyRefused("an action must be an object")
    name = str(action.get("name", "")).strip().lower()
    if not name:
        raise PolicyRefused("an action needs a name")
    if name in FORBIDDEN or any(word in name for word in FORBIDDEN):
        raise PolicyRefused(f"{name}: shell actions are never executed, on any host")
    if name in CONTROL:
        return {"control": CONTROL[name], "action": name}
    if name in UNSUPPORTED:
        raise UnsupportedAction(name, UNSUPPORTED[name])
    if name not in SUPPORTED:
        raise UnsupportedAction(name, "not in the AEP operation vocabulary")
    operation = SUPPORTED[name]
    if operation not in desktop_providers.OPERATIONS:
        # The two lists drifting apart would silently widen what a model may do.
        raise PolicyRefused(f"{operation} is not a registered desktop operation")
    arguments: dict[str, Any] = {}
    if name == "click":
        x, y = action.get("x"), action.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise PolicyRefused("click needs numeric x and y")
        if not (0 <= x <= COORDINATE_SCALE and 0 <= y <= COORDINATE_SCALE):
            raise PolicyRefused(f"click coordinates must be within [0, {COORDINATE_SCALE}]")
        arguments = {"x": round(float(x) * window.width / COORDINATE_SCALE),
                     "y": round(float(y) * window.height / COORDINATE_SCALE)}
        if not window.contains(arguments["x"], arguments["y"]):
            raise PolicyRefused("click lands outside the window")
    elif name == "type":
        text = action.get("text")
        if not isinstance(text, str) or not text.strip():
            raise PolicyRefused("type needs non-empty text")
        arguments = {"text": text}
    elif name == "keypress":
        keys = action.get("keys") or action.get("key")
        if not isinstance(keys, str) or not keys.strip():
            raise PolicyRefused("keypress needs keys")
        arguments = {"keys": keys}
    elif name == "wait":
        seconds = action.get("seconds", 0.5)
        if not isinstance(seconds, (int, float)) or seconds < 0 or seconds > 30:
            raise PolicyRefused("wait must be between 0 and 30 seconds")
        arguments = {"seconds": float(seconds)}
    return {"operation": operation, "arguments": arguments, "action": name}


class PolicyExecutor:
    """Drive a policy against one window, under AEP's rules.

    ``execute`` is injected: in production it is the MCP binding for the
    operation, in tests it is a recorder.  ``verify`` is injected too, and its
    absence is not permission to continue -- an unverified step ends the run
    either way, because "no verifier" cannot mean "assume it worked".
    """

    def __init__(self, execute: Callable[[str, dict], dict], *, window: vision_grounding.Region,
                 verify: Callable[[Step], bool] | None = None, max_steps: int = 15,
                 allowed: set[str] | None = None):
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.execute = execute
        self.window = window
        self.verify = verify
        self.max_steps = max_steps
        self.allowed = set(allowed) if allowed is not None else set(desktop_providers.OPERATIONS)
        self.steps: list[Step] = []

    def run(self, policy: Any, *, task: str = "") -> dict[str, Any]:
        self.steps = []
        stopped = "step-budget-exhausted"
        for index in range(self.max_steps):
            proposal = policy.next_action({"task": task, "steps": [s.as_dict() for s in self.steps],
                                           "window": self.window.as_dict()})
            if proposal is None:
                stopped = "policy-finished"
                break
            try:
                instruction = normalise(proposal, window=self.window)
            except UnsupportedAction as exc:
                stopped = f"UNSUPPORTED_ACTION:{exc.action}"
                self.steps.append(Step(index=index, action=str(proposal.get("name", "")),
                                       operation="", arguments={}, note=str(exc)))
                break
            except PolicyRefused as exc:
                stopped = f"REFUSED:{exc}"
                self.steps.append(Step(index=index, action=str(proposal.get("name", "")),
                                       operation="", arguments={}, note=str(exc)))
                break
            if "control" in instruction:
                stopped = instruction["control"]
                break
            operation = instruction["operation"]
            if operation not in self.allowed:
                stopped = f"OPERATION_NOT_ALLOWED:{operation}"
                self.steps.append(Step(index=index, action=instruction["action"],
                                       operation=operation, arguments=instruction["arguments"],
                                       note="not in the executor allowlist"))
                break
            step = Step(index=index, action=instruction["action"], operation=operation,
                        arguments=instruction["arguments"])
            try:
                step.result = self.execute(operation, instruction["arguments"]) or {}
            except Exception as exc:  # noqa: BLE001 - a driver failure is a step failure
                step.note = f"driver failed: {type(exc).__name__}"
                step.verified = False
                self.steps.append(step)
                stopped = "DRIVER_FAILED"
                break
            step.verified = bool(self.verify(step)) if self.verify else None
            self.steps.append(step)
            if self.verify is not None and not step.verified:
                stopped = "step-not-verified"
                break
        return {"state": "STOPPED" if stopped != "policy-finished" else "COMPLETED",
                "stopped_because": stopped, "steps": [s.as_dict() for s in self.steps],
                "note": "the executor never decides that a step worked; the evidence ladder does"}


class ScriptedPolicy:
    """A policy that replays a fixed plan, for tests and for recorded traces."""

    def __init__(self, plan: list[dict[str, Any]]):
        self.plan = list(plan)
        self.seen = 0

    def next_action(self, state: dict[str, Any]) -> dict[str, Any] | None:
        if self.seen >= len(self.plan):
            return None
        action = self.plan[self.seen]
        self.seen += 1
        return action
