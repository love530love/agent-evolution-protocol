"""Small dependency-aware mission graph for resumable agent operations."""
from __future__ import annotations
from typing import Any
import json
from datetime import datetime, timezone
from pathlib import Path

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

def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def save(path: Path, graph: dict[str, Any], *, event: dict[str, Any] | None = None) -> dict[str, Any]:
    """Atomically persist graph and append an optional event for crash recovery."""
    validate(graph)
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(graph)
    payload["schema"] = "aep-mission-graph-v1"
    payload["updated_at"] = _now()
    if event:
        events_path = path.with_suffix(path.suffix + ".events.jsonl")
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": _now(), **event}, ensure_ascii=False, sort_keys=True) + "\n")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return payload

def load(path: Path) -> dict[str, Any]:
    """Load and validate a persisted graph; reject corrupt state."""
    path = Path(path)
    graph = json.loads(path.read_text(encoding="utf-8"))
    validate(graph)
    return graph

def recover_events(path: Path) -> list[dict[str, Any]]:
    events_path = Path(path).with_suffix(Path(path).suffix + ".events.jsonl")
    if not events_path.exists(): return []
    events = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if line.strip(): events.append(json.loads(line))
    return events
