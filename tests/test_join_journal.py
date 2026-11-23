from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_evolution_protocol.coordination import conversation_journal, join_journal, journal_projection, workspace_members


W_A = "W-" + "a" * 16
W_B = "W-" + "b" * 16


def _join(root, workspace=W_A, agent="qoder"):
    return workspace_members.join(root, workspace_id=workspace, agent=agent, source="self-report")


def test_project_is_idempotent_scoped_and_keeps_source(tmp_path):
    _join(tmp_path)
    _join(tmp_path, W_B, "codex")
    first = join_journal.project(tmp_path, workspace_id=W_A)
    second = join_journal.project(tmp_path, workspace_id=W_A)
    assert first["events"][0]["created"] is True
    assert second["events"][0]["created"] is False
    assert first["events"][0]["event_id"] == second["events"][0]["event_id"]
    assert workspace_members.notifications(tmp_path, workspace_id=W_A)["count"] == 1
    assert first["delivery"] == "NOT_ATTEMPTED"
    assert first["agent_ack"] is False
    events = conversation_journal.read_events(tmp_path, include_raw=True)
    assert len(events) == 1
    assert events[0]["workspace_id"] == W_A
    assert events[0]["raw_hook"]["source"] == "self-report"
    db = tmp_path / "index.sqlite3"
    assert journal_projection.rebuild(tmp_path, db)["indexed"] == 1
    assert journal_projection.search(db, workspace_id=W_A, contains="qoder")["events"][0]["kind"] == "member_joined"
    assert journal_projection.search(db, workspace_id=W_B)["events"] == []


def test_replay_after_crash_before_and_after_publication(tmp_path, monkeypatch):
    _join(tmp_path)
    actual_link = os.link
    monkeypatch.setattr(join_journal.os, "link", lambda *args: (_ for _ in ()).throw(OSError("before")))
    with pytest.raises(OSError, match="before"):
        join_journal.project(tmp_path, workspace_id=W_A)
    assert workspace_members.notifications(tmp_path, workspace_id=W_A)["count"] == 1
    assert conversation_journal.read_events(tmp_path) == []
    monkeypatch.setattr(join_journal.os, "link", actual_link)
    created = join_journal.project(tmp_path, workspace_id=W_A)
    # A crash after publication but before the caller saves its source cursor
    # requires replay; it must reuse the exact journal record, not duplicate it.
    replayed = join_journal.project(tmp_path, workspace_id=W_A)
    assert created["events"][0]["created"] is True
    assert replayed["events"][0]["created"] is False
    assert len(conversation_journal.read_events(tmp_path)) == 1


def test_failed_batch_replay_does_not_drop_or_duplicate(tmp_path, monkeypatch):
    _join(tmp_path, agent="qoder")
    _join(tmp_path, agent="codex")
    publish = join_journal._publish
    calls = []
    def stop_on_second(folder, event):
        calls.append(event["id"])
        if len(calls) == 2:
            raise OSError("mid-batch failure")
        return publish(folder, event)
    monkeypatch.setattr(join_journal, "_publish", stop_on_second)
    with pytest.raises(OSError, match="mid-batch failure"):
        join_journal.project(tmp_path, workspace_id=W_A)
    monkeypatch.setattr(join_journal, "_publish", publish)
    replay = join_journal.project(tmp_path, workspace_id=W_A)
    assert [item["created"] for item in replay["events"]] == [False, True]
    assert len(conversation_journal.read_events(tmp_path)) == 2


def test_concurrent_projectors_never_replace_or_duplicate(tmp_path):
    _join(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: join_journal.project(tmp_path, workspace_id=W_A), range(16)))
    assert sum(result["events"][0]["created"] for result in results) == 1
    assert len(conversation_journal.read_events(tmp_path)) == 1


def test_concurrent_process_projectors_share_one_journal_file(tmp_path):
    _join(tmp_path)
    env = {**os.environ, "PYTHONUTF8": "1",
           "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    command = [sys.executable, "-m", "agent_evolution_protocol.coordination.join_journal",
               "--root", str(tmp_path), "--workspace-id", W_A]
    children = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, encoding="utf-8", env=env,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for _ in range(6)]
    outputs = []
    for child in children:
        output, error = child.communicate(timeout=30)
        assert child.returncode == 0, error
        outputs.append(json.loads(output))
    assert sum(row["events"][0]["created"] for row in outputs) == 1
    assert len(conversation_journal.read_events(tmp_path)) == 1


def test_existing_different_event_is_never_overwritten(tmp_path):
    _join(tmp_path)
    source = workspace_members.notifications(tmp_path, workspace_id=W_A)["events"][0]
    event = join_journal._event(source, "agent-legion")
    folder = conversation_journal.archive_root(tmp_path) / "events"
    folder.mkdir(parents=True)
    target = folder / (event["id"] + ".json")
    target.write_text(json.dumps({"id": event["id"], "text": "someone else"}), encoding="utf-8")
    with pytest.raises(ValueError, match="JOURNAL_EVENT_ID_COLLISION"):
        join_journal.project(tmp_path, workspace_id=W_A)
    assert json.loads(target.read_text(encoding="utf-8"))["text"] == "someone else"


def test_empty_source_does_not_create_a_journal(tmp_path):
    result = join_journal.project(tmp_path, workspace_id=W_A)
    assert result["events"] == []
    assert not (tmp_path / "private_conversations").exists()


def test_pagination_and_distinct_rooms(tmp_path):
    _join(tmp_path, agent="qoder")
    _join(tmp_path, agent="codex")
    first = join_journal.project(tmp_path, workspace_id=W_A, limit=1)
    second = join_journal.project(tmp_path, workspace_id=W_A, limit=1, after=first["next_cursor"])
    assert first["events"][0]["sequence"] < second["events"][0]["sequence"]
    other_room = join_journal.project(tmp_path, workspace_id=W_A, limit=1, room="team-b")
    assert other_room["events"][0]["event_id"] != first["events"][0]["event_id"]
    assert len(conversation_journal.read_events(tmp_path)) == 3
