"""Accept one input and turn it into work the whole swarm can finish alone.

The operator's complaint is that every task costs a conversation per agent.
:func:`intake` replaces those conversations with one call: it anchors the
request, writes the DAG, and hands the first wave to agents that can start now.
Everything after that is the supervisor's job, not the operator's.

Two shapes are supported on purpose:

* ``single`` -- one node, one owner.  The honest default.  Most requests do not
  need a committee, and inventing one wastes agent budget on ceremony.
* ``reviewed`` -- execute then an *independent* review by a different agent.
  This is the shape the workspace already uses for its own changes, so it is
  offered rather than invented.

A free-text request cannot be decomposed correctly without reading it, and this
module refuses to pretend otherwise: multi-step plans come from ``steps``
(explicit) or from a planner task whose deliverable is the plan.
"""
from __future__ import annotations

from pathlib import Path

from agent_evolution_protocol.federation import assignment, engine

INTAKE_SCHEMA = "aep-intake-v1"
SHAPES = ("single", "reviewed")


def plan_for(text, agents, *, shape="single"):
    """Return the node list for one request.

    Node ids are stable (``execute`` / ``review``) so a re-run of the same
    intake on the same workspace adds nothing instead of duplicating work.
    """
    if shape not in SHAPES:
        raise ValueError(f"shape must be one of {SHAPES}")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a request is required")
    if not agents:
        raise ValueError("at least one agent is required")
    nodes = [{"id": "execute", "requirement": text.strip(), "deps": [], "paths": [],
              "capability": "execute"}]
    if shape == "reviewed":
        reviewer = agents[1] if len(agents) > 1 else agents[0]
        nodes.append({"id": "review", "deps": ["execute"], "paths": [],
                      "capability": "execute",
                      "requirement": ("独立复核 execute 的产出，不要复述执行者的结论。"
                                      "给出可复算的反例或明确通过。"),
                      "preferred": reviewer})
    return nodes


def intake(root, coord_root, *, text, agents, workspace_id="", goal="",
           acceptance=None, shape="single", steps=None, capabilities=None,
           sender="kernel", priority="normal", ttl_hours=24.0, moment=None,
           transport="auto", allow_launch=False, urgent_interject=False):
    """Anchor the request, write the DAG, and start the first wave."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a request is required")
    agents = [str(item).strip() for item in agents if str(item).strip()]
    if not agents:
        raise ValueError("at least one agent is required")
    root = Path(root)
    # A provisional ID must still be a legal one: the workspace watermark is
    # validated everywhere it travels, so a made-up "WS-INTAKE-<time>" string
    # would be rejected by the first order that carried it.
    from agent_evolution_protocol.coordination.workspace_identity import new_workspace_id
    workspace_id = workspace_id or new_workspace_id()
    evidence = []

    try:
        workspace = engine.load(root, workspace_id)
        evidence.append("workspace:existing")
    except (OSError, ValueError):
        workspace = engine.create(root, goal=goal or text.strip(),
                                  acceptance=list(acceptance or [text.strip()]),
                                  moment=moment)
        evidence.append("workspace:created")
        workspace_id = workspace["workspace_id"]

    nodes = steps if steps is not None else plan_for(text, agents, shape=shape)
    if not isinstance(nodes, (list, tuple)) or not nodes:
        raise ValueError("steps must be a non-empty list")

    added, already = [], []
    for node in nodes:
        node_id = str(node.get("id", "")).strip()
        if not node_id:
            raise ValueError("every step needs an id")
        if node_id in workspace["tasks"]:
            already.append(node_id)
            continue
        engine.add_task(root, workspace, task_id=node_id,
                        requirement=str(node.get("requirement", "")),
                        deps=list(node.get("deps") or []),
                        capability=str(node.get("capability", "")),
                        preferred=str(node.get("preferred", "")), moment=moment)
        added.append(node_id)

    workspace = engine.load(root, workspace_id)
    started = []
    for node_id in engine.ready_tasks(workspace):
        node = next((item for item in nodes if str(item.get("id")) == node_id), None)
        if node is None:
            continue
        target = _pick(node, agents, capabilities)
        receipt = assignment.assign(
            coord_root, workspace_id=workspace_id, task_id=node_id,
            requirement=str(node.get("requirement", "")), target=target,
            paths=list(node.get("paths") or []), deps=list(node.get("deps") or []),
            sender=sender, priority=priority, ttl_hours=ttl_hours,
            capability=str(node.get("capability", "")),
            preferred=str(node.get("preferred", "")),
            allow_launch=allow_launch, transport=transport,
            urgent_interject=urgent_interject, federation_root=str(root),
            workspace=workspace, moment=moment)
        workspace = engine.load(root, workspace_id)
        started.append({"task": node_id, "agent": target, "order": receipt["order"]["id"],
                        "event_id": receipt["event_id"]})

    return {"schema": INTAKE_SCHEMA, "workspace": workspace_id, "request": text.strip(),
            "shape": shape, "agents": agents, "added": added, "already_present": already,
            "started": started, "evidence": evidence,
            "next": ("运行 `aep federate serve` 让心跳接管；它会派工、回读、失败换人，"
                     "直到每个节点 done/failed/blocked。")}


def _pick(node, agents, capabilities):
    """Choose an owner: explicit preference, then declared capability, then order."""
    preferred = str(node.get("preferred") or "").strip()
    if preferred and preferred in agents:
        return preferred
    needed = str(node.get("capability") or "").strip()
    if needed and capabilities:
        for agent in agents:
            if needed in (capabilities.get(agent) or []):
                return agent
    return agents[0]
