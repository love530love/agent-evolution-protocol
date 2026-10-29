from pathlib import Path
from agent_evolution_protocol.coordination.mission_intake import create, load


def test_create_persists_mission_contract(tmp_path: Path):
    m = create(tmp_path, goal="build a verified release", workspace="K:/work",
               deliverables=["report.md"], constraints=["no blind upload"])
    assert m["mission_id"].startswith("M-")
    assert load(tmp_path, m["mission_id"])["stages"][-1]["stage_id"] == "delivery"


def test_goal_required(tmp_path: Path):
    try:
        create(tmp_path, goal=" ")
    except ValueError:
        return
    raise AssertionError("empty goal must fail")
