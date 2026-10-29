from pathlib import Path
from agent_evolution_protocol.coordination.mission_intake import create
from agent_evolution_protocol.coordination.mission_planner import plan


def test_baseline_plan_is_dependency_ordered(tmp_path: Path):
    m = create(tmp_path, goal="ship", deliverables=["report.md"])
    out = plan(tmp_path, m["mission_id"])
    assert [s["stage_id"] for s in out["stages"]] == ["research", "implementation", "validation", "delivery"]
    assert out["stages"][-1]["depends_on"] == ["validation"]
