"""Adapter boundary between the federation kernel and any vendor agent.

The kernel never imports a vendor SDK and never parses a chat transcript. It
talks to adapters, and an adapter is only allowed to report facts:

* ``capabilities()`` — what this agent can be asked to do;
* ``heartbeat(now)`` — is it alive, when did it last progress;
* ``deliver(task)`` — accepted / refused / failed, plus a short note;
* ``observe()`` — current state and last output digest.

Everything a vendor needs (A2A, MCP, CDP, UIA, HTTP) lives inside an adapter.
``FakeAdapter`` is the reference implementation used by the tests; a real
adapter has to satisfy the same four calls and nothing more.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


DELIVERY_STATES = ("accepted", "refused", "failed")
#: Enumerations copied from ``schemas/agent-capability-bundle-v1.schema.json``
#: so a bundle is rejected here rather than by a downstream validator.
CONNECTOR_KINDS = frozenset({"app", "native", "browser", "a2a", "mcp", "cli",
                             "webhook", "clawchat"})
TRANSPORTS = frozenset({"stdio", "http", "sse", "ws", "uia", "cdp", "file", "named-pipe"})


@runtime_checkable
class Adapter(Protocol):
    agent: str

    def capabilities(self) -> set[str]: ...
    def heartbeat(self, now) -> dict: ...
    def deliver(self, task: dict) -> dict: ...
    def observe(self) -> dict: ...


class FakeAdapter:
    """Deterministic adapter: replays a scripted list of outcomes.

    An outcome is either a delivery state (``accepted``/``refused``/``failed``)
    or any other non-empty string, which is reported as ``failed`` with that
    string as the ``signal``. The second form matters: the diagnosis table knows
    ten signals and only one of them would otherwise be reachable from a test.

    When the script runs out the last entry repeats, so a test never depends on
    call counts it did not declare.
    """

    def __init__(self, agent, capabilities, *, outcomes=("accepted",), heartbeat_at=None):
        if not isinstance(agent, str) or not agent.strip():
            raise ValueError("agent is required")
        self.agent = agent.strip()
        self._capabilities = {str(item) for item in capabilities}
        self._outcomes = list(outcomes) or ["accepted"]
        for item in self._outcomes:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("scripted outcomes must be non-empty strings")
        self._index = 0
        self.last_heartbeat = heartbeat_at
        self.deliveries = []

    def capabilities(self):
        return set(self._capabilities)

    def heartbeat(self, now):
        return {"agent": self.agent, "alive": True,
                "last_progress_at": self.last_heartbeat, "now": now}

    def deliver(self, task):
        outcome = self._outcomes[min(self._index, len(self._outcomes) - 1)]
        self._index += 1
        self.deliveries.append({"task": task.get("id"), "outcome": outcome})
        if outcome in DELIVERY_STATES:
            return {"status": outcome, "signal": "", "note": f"scripted {outcome}"}
        return {"status": "failed", "signal": outcome,
                "note": f"scripted {outcome}"}

    def observe(self):
        return {"agent": self.agent, "state": "idle" if not self.deliveries else "worked",
                "deliveries": len(self.deliveries)}


def match(adapters, requirement, *, exclude=()):
    """Return agents holding the required capability, in stable order."""
    if not isinstance(requirement, str) or not requirement.strip():
        raise ValueError("requirement is required")
    blocked = {item for item in exclude}
    return sorted(adapter.agent for adapter in adapters
                  if adapter.agent not in blocked and requirement in adapter.capabilities())


def find(adapters, agent):
    for adapter in adapters:
        if adapter.agent == agent:
            return adapter
    return None


def bundle(adapter, *, workspace_id=None, transport="stdio", kind="native",
           client=None, acceptance=None, priority=100):
    """Render an adapter as an ``agent-capability-bundle-v1`` document.

    The kernel does not need this to schedule, but a newcomer does: it answers
    "who can be asked what, over which transport" in the format the rest of the
    workspace already reads, instead of inventing a private capability file.
    """
    if not isinstance(adapter, Adapter):
        raise ValueError("adapter must expose capabilities/heartbeat/deliver/observe")
    if kind not in CONNECTOR_KINDS:
        raise ValueError(f"unknown connector kind {kind!r}")
    if transport not in TRANSPORTS:
        raise ValueError(f"unknown transport {transport!r}")
    capabilities = sorted({str(item) for item in adapter.capabilities()})
    if not capabilities:
        raise ValueError("adapter advertises no capabilities")
    document = {
        "schema": "aep-agent-capability-bundle-v1",
        "agent": adapter.agent,
        "display_name": adapter.agent,
        "client": dict(client or {"kind": "federation-adapter", "transport": transport}),
        "servers": [],
        "skills": [],
        "connectors": [{"name": adapter.agent, "kind": kind, "transport": transport,
                        "enabled": True, "workspace_scoped": True,
                        "session_scoped": False, "capabilities": capabilities}],
        "capabilities": capabilities,
        "locator": {"adapter": type(adapter).__name__, "module": type(adapter).__module__},
        "fallbacks": [],
        "acceptance": dict(acceptance or {"delivers": True, "reports_signal": True}),
        "priority": int(priority),
    }
    if workspace_id:
        document["workspace_id"] = workspace_id
    return document
