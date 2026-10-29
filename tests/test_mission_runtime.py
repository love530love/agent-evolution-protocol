from pathlib import Path
from agent_evolution_protocol.coordination.mission_intake import create
from agent_evolution_protocol.coordination.mission_runtime import transition, checkpoint


def test_stage_dependencies_and_checkpoint(tmp_path: Path):
    m = create(tmp_path, goal="x")
    transition(tmp_path, mission_id=m["mission_id"], stage_id="intake", state="completed", actor="planner")
    transition(tmp_path, mission_id=m["mission_id"], stage_id="execution", state="running", actor="codex")
    cp = checkpoint(tmp_path, mission_id=m["mission_id"], stage_id="execution", actor="codex", payload={"step": 1})
    assert cp["payload"]["step"] == 1


def test_unmet_dependency_blocks(tmp_path: Path):
    m = create(tmp_path, goal="x")
    try:
        transition(tmp_path, mission_id=m["mission_id"], stage_id="delivery", state="running", actor="x")
    except ValueError as e:
        assert "dependency" in str(e)
        return
    raise AssertionError("dependency must be enforced")
