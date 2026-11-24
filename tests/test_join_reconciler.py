from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from agent_evolution_protocol.coordination import conversation_journal, join_reconciler, workspace_members


W_A = "W-" + "a" * 16
W_B = "W-" + "b" * 16


def joined(root, agent, workspace=W_A):
    return workspace_members.join(root, workspace_id=workspace, agent=agent, source="test")


def test_page_replays_and_isolates_workspace_and_room(tmp_path):
    a = joined(tmp_path, "qoder")
    joined(tmp_path, "codex", W_B)
    b = joined(tmp_path, "workbuddy")
    one = join_reconciler.reconcile(tmp_path, workspace_id=W_A, limit=1)
    assert one["previous_sequence"] == 0 and one["sequence"] == a["event_seq"]
    assert not one["agent_ack"] and not one["notification_delivered"]
    assert join_reconciler.status(tmp_path, workspace_id=W_A)["sequence"] == a["event_seq"]
    two = join_reconciler.reconcile(tmp_path, workspace_id=W_A, limit=1)
    assert two["sequence"] == b["event_seq"]
    assert join_reconciler.reconcile(tmp_path, workspace_id=W_A)["status"] == "IDLE"
    assert join_reconciler.status(tmp_path, workspace_id=W_B)["sequence"] == 0
    assert join_reconciler.status(tmp_path, workspace_id=W_A, room="other")["sequence"] == 0
    assert len(conversation_journal.read_events(tmp_path)) == 2


def test_crash_after_journal_before_cursor_commit_replays_without_duplicate(tmp_path, monkeypatch):
    joined(tmp_path, "qoder")
    actual = join_reconciler._write_cursor
    monkeypatch.setattr(join_reconciler, "_write_cursor", lambda *a: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError, match="crash"):
        join_reconciler.reconcile(tmp_path, workspace_id=W_A)
    assert join_reconciler.status(tmp_path, workspace_id=W_A)["sequence"] == 0
    assert len(conversation_journal.read_events(tmp_path)) == 1
    monkeypatch.setattr(join_reconciler, "_write_cursor", actual)
    replay = join_reconciler.reconcile(tmp_path, workspace_id=W_A)
    assert replay["status"] == "PROJECTED"
    assert replay["events"][0]["created"] is False
    assert len(conversation_journal.read_events(tmp_path)) == 1


def test_corrupt_cursor_or_changed_source_never_advances(tmp_path):
    first = joined(tmp_path, "qoder")
    join_reconciler.reconcile(tmp_path, workspace_id=W_A)
    with sqlite3.connect(tmp_path / workspace_members.DB_NAME) as db:
        db.execute("UPDATE join_events SET agent='other' WHERE seq=?", (first["event_seq"],))
    with pytest.raises(ValueError, match="SOURCE_CURSOR_MISMATCH"):
        join_reconciler.reconcile(tmp_path, workspace_id=W_A)
    assert join_reconciler.status(tmp_path, workspace_id=W_A)["sequence"] == first["event_seq"]
    path = next((tmp_path / "join_reconciler").glob("*.json"))
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="CURSOR_UNREADABLE"):
        join_reconciler.reconcile(tmp_path, workspace_id=W_A)


def test_status_does_not_create_root_and_missing_source_is_not_success(tmp_path):
    root = tmp_path / "absent"
    assert join_reconciler.status(root, workspace_id=W_A)["sequence"] == 0
    assert not root.exists()
    assert join_reconciler.reconcile(root, workspace_id=W_A)["status"] == "SOURCE_ABSENT"
    assert not list((root / "join_reconciler").glob("*.json"))


def test_concurrent_workers_serialize_and_never_duplicate(tmp_path):
    joined(tmp_path, "qoder")
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(lambda _: join_reconciler.reconcile(tmp_path, workspace_id=W_A), range(6)))
    assert sum(row["status"] == "PROJECTED" for row in rows) == 1
    assert len(conversation_journal.read_events(tmp_path)) == 1


def test_multiprocess_workers_share_cursor(tmp_path):
    joined(tmp_path, "qoder")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    command = [sys.executable, "-m", "agent_evolution_protocol.coordination.join_reconciler", "reconcile",
               "--root", str(tmp_path), "--workspace-id", W_A]
    children = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 encoding="utf-8", env=env,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) for _ in range(4)]
    results = []
    for child in children:
        output, error = child.communicate(timeout=30)
        assert child.returncode == 0, error
        results.append(json.loads(output))
    assert sum(row["status"] == "PROJECTED" for row in results) == 1
    assert len(conversation_journal.read_events(tmp_path)) == 1


def test_projection_failure_never_advances_cursor(tmp_path, monkeypatch):
    joined(tmp_path, "qoder")
    monkeypatch.setattr(join_reconciler.join_journal, "project",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("write denied")))
    with pytest.raises(OSError, match="write denied"):
        join_reconciler.reconcile(tmp_path, workspace_id=W_A)
    assert join_reconciler.status(tmp_path, workspace_id=W_A)["sequence"] == 0
