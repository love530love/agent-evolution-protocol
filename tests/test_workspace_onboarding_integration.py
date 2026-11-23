"""Exercise the explicit binding, join, journal and index as one flow."""
from agent_evolution_protocol.coordination import (
    join_journal, journal_projection, workspace_binding, workspace_join,
)
from agent_evolution_protocol.federation import engine


def test_join_is_indexed_once_and_isolated_by_existing_workspace(tmp_path):
    project = tmp_path / "project"
    federation = tmp_path / "federation"
    project.mkdir()
    federation.mkdir()
    coord = project / "coordination"
    first = engine.create(federation, goal="first", acceptance=["done"])["workspace_id"]
    second = engine.create(federation, goal="second", acceptance=["done"])["workspace_id"]
    workspace_binding.bind(coord, federation, project, first)
    workspace_binding.bind(coord, federation, project, second)
    joined = workspace_join.join(coord, project, workspace_id=first, agent="qoder", session_id="s1")
    assert joined["membership"]["created"] is True
    assert joined["notification_delivered"] is False
    assert joined["agent_ack"] is False
    assert workspace_join.join(coord, project, workspace_id=first, agent="qoder")["membership"]["created"] is False
    assert join_journal.project(coord, workspace_id=first)["events"][0]["created"] is True
    assert join_journal.project(coord, workspace_id=first)["events"][0]["created"] is False
    database = coord / "journal-index.sqlite3"
    assert journal_projection.rebuild(coord, database)["indexed"] == 1
    assert len(journal_projection.search(database, workspace_id=first, contains="qoder")["events"]) == 1
    assert journal_projection.search(database, workspace_id=second, contains="qoder")["events"] == []
