from pathlib import Path
from agent_evolution_protocol.coordination.mission_intake import create
from agent_evolution_protocol.coordination.mission_supervisor import inspect


def test_supervisor_assigns_ready_stage(tmp_path: Path):
    create(tmp_path, goal="x")
    state = inspect(tmp_path)
    assert state["actions"][0]["action"] == "assign"


def test_supervisor_is_model_free_and_empty_safe(tmp_path: Path):
    assert inspect(tmp_path) == {"missions": 0, "actions": []}
