import pytest
from agent_evolution_protocol.coordination.mission_graph import ready_nodes, transition, save, load, recover_events, advance

def test_graph_unlocks_next_node():
    graph = {"nodes": [{"id": "wake"}, {"id": "execute", "depends_on": ["wake"]}]}
    assert ready_nodes(graph) == ["wake"]
    transition(graph, "wake", "done")
    assert ready_nodes(graph) == ["execute"]

def test_graph_rejects_work_before_dependency():
    graph = {"nodes": [{"id": "a"}, {"id": "b", "depends_on": ["a"]}]}
    with pytest.raises(ValueError): transition(graph, "b", "working")

def test_graph_persists_and_recovers_events(tmp_path):
    path = tmp_path / "mission.json"
    graph = {"nodes": [{"id": "wake"}]}
    save(path, graph, event={"kind": "created", "node": "wake"})
    transition(graph, "wake", "done")
    save(path, graph, event={"kind": "transition", "node": "wake", "state": "done"})
    assert load(path)["nodes"][0]["state"] == "done"
    assert len(recover_events(path)) == 2

def test_ack_unlocks_successor_and_failure_is_recorded(tmp_path):
    path = tmp_path / "mission.json"
    save(path, {"nodes": [{"id": "wake"}, {"id": "execute", "depends_on": ["wake"]}]})
    result = advance(path, "wake", "ack", actor="qoder")
    assert result["nodes"][1].get("state", "pending") == "pending"
    assert ready_nodes(result) == ["execute"]
    advance(path, "execute", "failed", actor="qoder")
    assert recover_events(path)[-1]["outcome"] == "failed"
