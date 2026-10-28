import pytest
from agent_evolution_protocol.coordination.mission_graph import ready_nodes, transition

def test_graph_unlocks_next_node():
    graph = {"nodes": [{"id": "wake"}, {"id": "execute", "depends_on": ["wake"]}]}
    assert ready_nodes(graph) == ["wake"]
    transition(graph, "wake", "done")
    assert ready_nodes(graph) == ["execute"]

def test_graph_rejects_work_before_dependency():
    graph = {"nodes": [{"id": "a"}, {"id": "b", "depends_on": ["a"]}]}
    with pytest.raises(ValueError): transition(graph, "b", "working")
