from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_evolution_protocol.coordination import workspace_members as registry

W_A = "W-" + "a" * 16
W_B = "W-" + "b" * 16


def test_reads_do_not_create_an_empty_registry(tmp_path):
    root = tmp_path / "coordination"
    assert registry.members(root, workspace_id=W_A)["members"] == []
    assert registry.notifications(root, workspace_id=W_A)["events"] == []
    assert not root.exists()


def test_join_notifies_once_and_preserves_first_attribution(tmp_path):
    first = registry.join(tmp_path, workspace_id=W_A, agent="qoder", source="self", session_id="original")
    second = registry.join(tmp_path, workspace_id=W_A, agent="qoder", source="different", session_id="other")
    assert first["created"] is True
    assert second["created"] is False
    assert first["event_seq"] == second["event_seq"]
    assert second["member"]["source"] == "self"
    assert second["member"]["session_id"] == "original"
    assert registry.members(tmp_path, workspace_id=W_A)["count"] == 1
    assert registry.notifications(tmp_path, workspace_id=W_A)["count"] == 1
    assert first["delivery"] == "NOT_ATTEMPTED"
    assert first["agent_ack"] is False


def test_workspace_isolation_and_pagination(tmp_path):
    registry.join(tmp_path, workspace_id=W_A, agent="qoder", source="test")
    registry.join(tmp_path, workspace_id=W_B, agent="qoder", source="test")
    registry.join(tmp_path, workspace_id=W_A, agent="codex", source="test")
    page1 = registry.notifications(tmp_path, workspace_id=W_A, limit=1)
    page2 = registry.notifications(tmp_path, workspace_id=W_A, after=page1["next_cursor"], limit=1)
    assert [page1["events"][0]["agent"], page2["events"][0]["agent"]] == ["qoder", "codex"]
    assert registry.notifications(tmp_path, workspace_id=W_B)["count"] == 1
    assert {row["agent"] for row in registry.members(tmp_path, workspace_id=W_A)["members"]} == {"qoder", "codex"}


def test_rejects_invalid_input_before_creating_files(tmp_path):
    root = tmp_path / "coord"
    for values in (
        {"workspace_id": "name-only", "agent": "qoder", "source": "self"},
        {"workspace_id": W_A, "agent": "../qoder", "source": "self"},
        {"workspace_id": W_A, "agent": "qoder", "source": " "},
    ):
        with pytest.raises(ValueError):
            registry.join(root, **values)
    for after in (-1, True):
        with pytest.raises(ValueError):
            registry.notifications(root, workspace_id=W_A, after=after)
    assert not root.exists()


def test_concurrent_join_is_idempotent(tmp_path):
    def do_join(_):
        return registry.join(tmp_path, workspace_id=W_A, agent="qoder", source="parallel")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(do_join, range(16)))
    assert sum(r["created"] for r in results) == 1
    assert len({r["event_seq"] for r in results}) == 1
    assert registry.members(tmp_path, workspace_id=W_A)["count"] == 1
    assert registry.notifications(tmp_path, workspace_id=W_A)["count"] == 1


def test_concurrent_cli_processes_create_one_durable_notice(tmp_path):
    import json
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONUTF8"] = "1"
    command = [sys.executable, "-m", "agent_evolution_protocol.coordination.workspace_members",
               "join", "--root", str(tmp_path), "--workspace-id", W_A,
               "--agent", "qoder", "--source", "parallel-process"]
    children = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, encoding="utf-8", env=env,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for _ in range(4)]
    results = []
    for child in children:
        output, error = child.communicate(timeout=30)
        assert child.returncode == 0, error
        results.append(json.loads(output))
    assert sum(result["created"] for result in results) == 1
    assert registry.notifications(tmp_path, workspace_id=W_A)["count"] == 1


def test_failed_notification_write_rolls_back_membership(tmp_path):
    import sqlite3
    registry.join(tmp_path, workspace_id=W_A, agent="qoder", source="test")
    with sqlite3.connect(tmp_path / registry.DB_NAME) as db:
        db.execute("CREATE TRIGGER fail_join BEFORE INSERT ON join_events "
                   "WHEN NEW.agent='codex' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        registry.join(tmp_path, workspace_id=W_A, agent="codex", source="test")
    assert [item["agent"] for item in registry.members(tmp_path, workspace_id=W_A)["members"]] == ["qoder"]
    assert registry.notifications(tmp_path, workspace_id=W_A)["count"] == 1


def test_main_cli_exposes_join_and_notifications(tmp_path, capsys):
    import json
    from agent_evolution_protocol import cli
    parser = cli.build_parser()
    join = parser.parse_args(["--coord-root", str(tmp_path), "coord-workspace-members",
                              "join", "--workspace-id", W_A, "--agent", "codex",
                              "--source", "agent-self-report", "--session-id", "s1"])
    cli.coord_workspace_members(join)
    assert json.loads(capsys.readouterr().out)["created"] is True
    read = parser.parse_args(["--coord-root", str(tmp_path), "coord-workspace-members",
                              "notifications", "--workspace-id", W_A])
    cli.coord_workspace_members(read)
    result = json.loads(capsys.readouterr().out)
    assert [item["agent"] for item in result["events"]] == ["codex"]
