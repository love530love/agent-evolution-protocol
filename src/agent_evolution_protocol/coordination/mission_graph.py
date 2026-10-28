"""Small dependency-aware mission graph for resumable agent operations."""
from __future__ import annotations
from typing import Any

STATES = {"pending", "ready", "working", "done", "failed", "paused"}

def validate(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    if not isinstance(nodes, list) or not nodes: raise ValueError("graph needs nodes")
    ids = [n.get("id") for n in nodes]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids): raise ValueError("node IDs must be unique")
    known = set(ids)
    for n in nodes:
        if not isinstance(n.get("depends_on", []), list) or any(d not in known or d == n["id"] for d in n.get("depends_on", [])): raise ValueError("invalid dependency")
        if n.get("state", "pending") not in STATES: raise ValueError("invalid node state")
    return graph

def ready_nodes(graph: dict[str, Any]) -> list[str]:
    validate(graph)
    by_id = {n["id"]: n for n in graph["nodes"]}
    return [n["id"] for n in graph["nodes"] if n.get("state", "pending") in {"pending", "ready"} and all(by_id[d].get("state") == "done" for d in n.get("depends_on", []))]

def transition(graph: dict[str, Any], node_id: str, state: str) -> dict[str, Any]:
    validate(graph)
    if state not in STATES: raise ValueError("invalid state")
    node = next((n for n in graph["nodes"] if n["id"] == node_id), None)
    if node is None: raise ValueError("unknown node")
    if state in {"working", "done"} and node_id not in ready_nodes(graph): raise ValueError("dependencies are not complete")
    node["state"] = state
    return graph
