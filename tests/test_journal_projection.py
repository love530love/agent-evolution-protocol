from pathlib import Path
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from agent_evolution_protocol.coordination import journal_projection as projection
from agent_evolution_protocol.coordination.conversation_journal import append_event


WORK_A = "W-" + "a" * 16
WORK_B = "W-" + "b" * 16


def event(root, text, workspace=None, room="team"):
    return append_event(root, room=room, actor="codex", source="test", kind="result",
                        text=text, workspace_id=workspace)


def test_main_cli_rebuilds_and_searches_only_selected_workspace(tmp_path, capsys):
    from agent_evolution_protocol import cli
    target = event(tmp_path, "可检索进展", WORK_A)
    event(tmp_path, "可检索进展", WORK_B)
    parser = cli.build_parser()
    prefix = ["--coord-root", str(tmp_path), "coord-journal-index"]
    args = parser.parse_args(prefix + ["rebuild"])
    cli.coord_journal_index(args)
    assert json.loads(capsys.readouterr().out)["indexed"] == 2
    args = parser.parse_args(prefix + ["search", "--workspace-id", WORK_A,
                                       "--contains", "可检索"])
    cli.coord_journal_index(args)
    assert [row["event_id"] for row in json.loads(capsys.readouterr().out)["events"]] == [target["id"]]
    args = parser.parse_args(prefix + ["status", "--workspace-id", WORK_A])
    with pytest.raises(cli.ProtocolError):
        cli.coord_journal_index(args)


def test_projection_is_scoped_and_preserves_source_evidence(tmp_path):
    a = event(tmp_path, "进展 A-1 (50%)", WORK_A)
    event(tmp_path, "进展 A-1 (50%)", WORK_B)
    unbound = event(tmp_path, "进展 A-1 (50%)")
    database = tmp_path / "index.sqlite3"
    report = projection.rebuild(tmp_path, database)
    assert report["indexed"] == 3
    assert report["unbound"] == 1
    result = projection.search(database, workspace_id=WORK_A, contains="a-1 (50%)")
    assert [row["event_id"] for row in result["events"]] == [a["id"]]
    assert Path(result["events"][0]["source_path"]).name == a["id"] + ".json"
    assert len(result["events"][0]["sha256"]) == 64
    assert result["events"][0]["source"] == "test"
    legacy = projection.search(database, unbound=True, contains="A-1")
    assert legacy["scope"] == "unbound"
    assert [row["event_id"] for row in legacy["events"]] == [unbound["id"]]
    with pytest.raises(ValueError, match="UNBOUND_WORKSPACE_CONFLICT"):
        projection.search(database, workspace_id=WORK_A, unbound=True)


def test_rebuild_idempotent_detects_corrupt_changed_and_deleted(tmp_path):
    first = event(tmp_path, "before", WORK_A)
    second = event(tmp_path, "remove", WORK_A)
    db = tmp_path / "index.sqlite3"
    assert projection.rebuild(tmp_path, db)["indexed"] == 2
    assert projection.rebuild(tmp_path, db)["indexed"] == 2
    folder = tmp_path / "private_conversations" / "events"
    first_path = folder / (first["id"] + ".json")
    first_path.write_text("{broken", encoding="utf-8")
    (folder / (second["id"] + ".json")).unlink()
    report = projection.rebuild(tmp_path, db)
    assert report["indexed"] == 0
    assert [x["reason"] for x in report["errors"]] == ["INVALID_JSON"]
    assert projection.search(db, workspace_id=WORK_A)["events"] == []
    assert projection.search(db, workspace_id=WORK_A)["projection_error_count"] == 1
    health = projection.status(db)
    assert health["indexed"] == 0
    assert health["error_count"] == 1
    assert health["errors"][0]["reason"] == "INVALID_JSON"


def test_missing_source_does_not_create_or_erase_index(tmp_path):
    db = tmp_path / "index.sqlite3"
    assert projection.rebuild(tmp_path, db)["status"] == "SOURCE_ABSENT"
    assert not db.exists()
    item = event(tmp_path, "source", WORK_A)
    projection.rebuild(tmp_path, db)
    source = tmp_path / "private_conversations" / "events"
    source.rename(tmp_path / "saved-events")
    assert projection.rebuild(tmp_path, db)["database_unchanged"] is True
    assert projection.search(db, workspace_id=WORK_A)["events"][0]["event_id"] == item["id"]


def test_interrupted_rebuild_rolls_back_existing_projection(tmp_path, monkeypatch):
    item = event(tmp_path, "first", WORK_A)
    db = tmp_path / "index.sqlite3"
    projection.rebuild(tmp_path, db)
    event(tmp_path, "second", WORK_A)
    monkeypatch.setattr(projection, "_event", lambda path: (_ for _ in ()).throw(RuntimeError("interrupt")))
    with pytest.raises(RuntimeError):
        projection.rebuild(tmp_path, db)
    assert [r["event_id"] for r in projection.search(db, workspace_id=WORK_A)["events"]] == [item["id"]]


def test_unreadable_source_directory_does_not_clear_index(tmp_path, monkeypatch):
    item = event(tmp_path, "first", WORK_A)
    db = tmp_path / "index.sqlite3"
    projection.rebuild(tmp_path, db)
    scan = projection.os.scandir
    source = projection._source(tmp_path)

    def deny(path):
        if Path(path) == source:
            raise PermissionError("denied")
        return scan(path)

    monkeypatch.setattr(projection.os, "scandir", deny)
    with pytest.raises(PermissionError):
        projection.rebuild(tmp_path, db)
    assert [r["event_id"] for r in projection.search(db, workspace_id=WORK_A)["events"]] == [item["id"]]


def test_query_pagination_and_workspace_validation(tmp_path):
    event(tmp_path, "one", WORK_A)
    event(tmp_path, "two", WORK_A)
    db = tmp_path / "index.sqlite3"
    projection.rebuild(tmp_path, db)
    first = projection.search(db, workspace_id=WORK_A, limit=1)
    second = projection.search(db, workspace_id=WORK_A, after=first["next_cursor"], limit=1)
    assert first["events"][0]["event_id"] != second["events"][0]["event_id"]
    with pytest.raises(ValueError):
        projection.search(db, workspace_id="")
    with pytest.raises(ValueError):
        projection.search(db, workspace_id=WORK_A, limit=0)


def test_invalid_event_id_or_workspace_is_reported_not_indexed(tmp_path):
    item = event(tmp_path, "hello", WORK_A)
    path = tmp_path / "private_conversations" / "events" / (item["id"] + ".json")
    row = json.loads(path.read_text(encoding="utf-8"))
    row["workspace_id"] = "not-a-watermark"
    path.write_text(json.dumps(row), encoding="utf-8")
    (path.parent / "bad.json").write_text("{}", encoding="utf-8")
    report = projection.rebuild(tmp_path, tmp_path / "index.sqlite3")
    assert report["indexed"] == 0
    assert {x["reason"] for x in report["errors"]} == {"INVALID_WORKSPACE_ID", "INVALID_FILENAME"}


def test_unrelated_sqlite_database_is_not_overwritten(tmp_path):
    event(tmp_path, "one", WORK_A)
    db = tmp_path / "other.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE unrelated(value TEXT)")
        conn.execute("INSERT INTO unrelated VALUES ('keep')")
    with pytest.raises(ValueError, match="DATABASE_NOT_JOURNAL_PROJECTION"):
        projection.rebuild(tmp_path, db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT value FROM unrelated").fetchone()[0] == "keep"


def test_index_cannot_be_repointed_to_another_coordination_root(tmp_path):
    root_a, root_b = tmp_path / "one", tmp_path / "two"
    item = event(root_a, "one", WORK_A)
    event(root_b, "two", WORK_B)
    db = tmp_path / "index with # and %.sqlite3"
    projection.rebuild(root_a, db)
    with pytest.raises(ValueError, match="PROJECTION_SOURCE_OR_SCHEMA_MISMATCH"):
        projection.rebuild(root_b, db)
    assert projection.search(db, workspace_id=WORK_A)["events"][0]["event_id"] == item["id"]


def test_module_entrypoint_rebuild_and_query(tmp_path):
    item = event(tmp_path, "你好 A-1", WORK_A)
    command = [sys.executable, "-m", "agent_evolution_protocol.coordination.journal_projection"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    rebuilt = subprocess.run(command + ["rebuild", "--coord-root", str(tmp_path)],
                             capture_output=True, text=True, encoding="utf-8", check=True, env=env)
    assert json.loads(rebuilt.stdout)["indexed"] == 1
    queried = subprocess.run(command + ["search", "--coord-root", str(tmp_path),
                                       "--workspace-id", WORK_A, "--contains", "A-1"],
                             capture_output=True, text=True, encoding="utf-8", check=True, env=env)
    assert json.loads(queried.stdout)["events"][0]["event_id"] == item["id"]
