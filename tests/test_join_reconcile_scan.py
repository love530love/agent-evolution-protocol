"""Core recovery uses explicit bindings and never claims agent notification."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from unittest.mock import patch

from agent_evolution_protocol.coordination import (
    conversation_journal, core_host, join_reconcile_scan, join_reconciler,
    workspace_binding, workspace_members,
)
from agent_evolution_protocol.federation import engine


def _setup(tmp_path, count=1):
    project = tmp_path / "project"
    federation = tmp_path / "federation"
    project.mkdir()
    federation.mkdir()
    coord = project / "coordination"
    ids = []
    for index in range(count):
        workspace = engine.create(federation, goal=f"task {index}", acceptance=["done"])
        ident = workspace["workspace_id"]
        workspace_binding.bind(coord, federation, project, ident)
        workspace_members.join(coord, workspace_id=ident, agent=f"agent{index}", source="test")
        ids.append(ident)
    return coord, federation, ids


def _cycle(root):
    with patch.object(core_host.campaign_tick, "tick", return_value={"ok": True}), \
         patch.object(core_host.mission_pulse, "pulse", return_value={"queued": []}) as pulse:
        result = core_host.cycle(root, bridge_root=root)
        assert pulse.call_count == 1
    return result


def test_core_recovers_join_after_commit_and_isolates_unbound(tmp_path):
    coord, _, ids = _setup(tmp_path)
    workspace_members.join(coord, workspace_id="W-" + "b" * 16, agent="unbound", source="test")
    value = _cycle(coord)
    assert value["join_reconciliation"]["status"] == "OK"
    assert value["ui_touched"] is False and value["dispatch_performed"] is False
    events = conversation_journal.read_events(coord)
    assert len(events) == 1 and events[0]["workspace_id"] == ids[0]
    assert value["join_reconciliation"]["notification_delivered"] is False
    assert value["join_reconciliation"]["agent_ack"] is False
    assert _cycle(coord)["join_reconciliation"]["rows"][0]["status"] == "IDLE"
    assert len(conversation_journal.read_events(coord)) == 1


def test_round_robin_persists_across_calls_and_does_not_starve(tmp_path):
    coord, _, ids = _setup(tmp_path, count=3)
    seen = []
    for _ in range(3):
        report = join_reconcile_scan.run(coord, max_workspaces=1)
        seen.append(report["rows"][0]["workspace_id"])
    assert set(seen) == set(ids)
    assert len(conversation_journal.read_events(coord)) == 3
    assert join_reconcile_scan.run(coord, max_workspaces=1)["rows"][0]["workspace_id"] == sorted(ids)[0]


def test_round_robin_survives_fresh_interpreter_processes(tmp_path):
    coord, _, ids = _setup(tmp_path, count=2)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    code = ("import json,sys; from pathlib import Path; "
            "from agent_evolution_protocol.coordination.join_reconcile_scan import run; "
            "print(json.dumps(run(Path(sys.argv[1]), max_workspaces=1)))")
    found = []
    for _ in range(2):
        process = subprocess.run([sys.executable, "-c", code, str(coord)], env=env,
                                 capture_output=True, text=True, encoding="utf-8", timeout=30,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert process.returncode == 0, process.stderr
        found.append(json.loads(process.stdout)["rows"][0]["workspace_id"])
    assert set(found) == set(ids)


def test_real_core_process_recovers_after_restart_without_duplicate(tmp_path):
    coord, _, ids = _setup(tmp_path)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    command = [sys.executable, "-m", "agent_evolution_protocol.coordination.core_host",
               "--coord-root", str(coord), "--bridge-root", str(coord),
               "--interval-seconds", "1", "--max-ticks", "1"]
    results = []
    for _ in range(2):
        process = subprocess.run(command, env=env, capture_output=True, text=True,
                                 encoding="utf-8", timeout=30,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert process.returncode == 0, process.stderr
        results.append(json.loads(process.stdout.splitlines()[-1]))
    assert results[0]["status"] == "CYCLE_COMPLETED"
    assert results[0]["join_reconciliation"]["rows"][0]["status"] == "PROJECTED"
    assert results[1]["join_reconciliation"]["rows"][0]["status"] == "IDLE"
    assert all(row["dispatch_performed"] is False and row["delivery_claim"] == "NONE" for row in results)
    assert len(conversation_journal.read_events(coord)) == 1
    assert join_reconciler.status(coord, workspace_id=ids[0])["sequence"] > 0


def test_one_bad_binding_does_not_stop_another_workspace(tmp_path):
    coord, federation, ids = _setup(tmp_path, count=2)
    engine._state_path(federation, ids[0]).unlink()
    report = join_reconcile_scan.run(coord, max_workspaces=2)
    assert report["status"] == "DEGRADED"
    assert {row["workspace_id"]: row["status"] for row in report["rows"]}[ids[0]] == "ERROR"
    events = conversation_journal.read_events(coord)
    assert len(events) == 1 and events[0]["workspace_id"] == ids[1]


def test_corrupt_scan_cursor_is_reported_without_stopping_core(tmp_path):
    coord, _, _ = _setup(tmp_path)
    state = coord / "join_reconciler" / "scan-state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{broken", encoding="utf-8")
    result = _cycle(coord)
    assert result["join_reconciliation"]["status"] == "ERROR"
    assert "SCAN_CURSOR_UNREADABLE" in result["join_reconciliation"]["error"]
    assert state.read_text(encoding="utf-8") == "{broken"
    assert conversation_journal.read_events(coord) == []


def test_crash_before_scan_checkpoint_replays_idempotently(tmp_path, monkeypatch):
    coord, _, ids = _setup(tmp_path)
    original = join_reconcile_scan._save_state
    def crash(*args):
        raise OSError("checkpoint failed")
    monkeypatch.setattr(join_reconcile_scan, "_save_state", crash)
    result = _cycle(coord)
    assert result["join_reconciliation"]["status"] == "ERROR"
    assert join_reconciler.status(coord, workspace_id=ids[0])["sequence"] > 0
    monkeypatch.setattr(join_reconcile_scan, "_save_state", original)
    assert _cycle(coord)["join_reconciliation"]["rows"][0]["status"] == "IDLE"
    assert len(conversation_journal.read_events(coord)) == 1


def test_expired_budget_after_one_journal_write_keeps_source_cursor_for_retry(tmp_path, monkeypatch):
    coord, _, ids = _setup(tmp_path)
    workspace_members.join(coord, workspace_id=ids[0], agent="second", source="test")
    real_publish = join_reconciler.join_journal._publish
    real_check = join_reconciler.join_journal._check_deadline
    published = []
    def publish_then_expire(folder, event):
        value = real_publish(folder, event)
        published.append(event["id"])
        return value
    def expire_after_write(deadline):
        if published:
            raise TimeoutError("JOIN_RECOVERY_BUDGET_EXCEEDED")
        return real_check(deadline)
    monkeypatch.setattr(join_reconciler.join_journal, "_publish", publish_then_expire)
    monkeypatch.setattr(join_reconciler.join_journal, "_check_deadline", expire_after_write)
    result = _cycle(coord)
    assert result["join_reconciliation"]["status"] == "DEGRADED"
    assert "JOIN_RECOVERY_BUDGET_EXCEEDED" in result["join_reconciliation"]["rows"][0]["error"]
    assert join_reconciler.status(coord, workspace_id=ids[0])["sequence"] == 0
    assert len(conversation_journal.read_events(coord)) == 1
    monkeypatch.setattr(join_reconciler.join_journal, "_publish", real_publish)
    monkeypatch.setattr(join_reconciler.join_journal, "_check_deadline", real_check)
    assert _cycle(coord)["join_reconciliation"]["status"] == "OK"
    assert len(conversation_journal.read_events(coord)) == 2


def test_locked_source_degrades_without_holding_core_for_default_sqlite_timeout(tmp_path):
    coord, _, ids = _setup(tmp_path)
    db = sqlite3.connect(coord / workspace_members.DB_NAME, timeout=1, isolation_level=None)
    try:
        db.execute("BEGIN EXCLUSIVE")
        start = time.monotonic()
        result = _cycle(coord)
        elapsed = time.monotonic() - start
        assert result["join_reconciliation"]["status"] == "DEGRADED"
        assert result["join_reconciliation"]["rows"][0]["status"] == "ERROR"
        assert elapsed < 10
    finally:
        db.rollback()
        db.close()
    assert _cycle(coord)["join_reconciliation"]["status"] == "OK"
    assert join_reconciler.status(coord, workspace_id=ids[0])["sequence"] > 0


def test_heartbeat_carries_degraded_recovery_without_claiming_delivery(tmp_path):
    from io import StringIO
    output = StringIO()
    with patch.object(core_host, "cycle", return_value={
        "missions": {"queued": []}, "federation": {"status": "SKIPPED"},
        "join_reconciliation": {"status": "DEGRADED", "rows": [{"status": "ERROR"}]},
    }):
        core_host.watch(tmp_path, bridge_root=tmp_path, max_ticks=1,
                        interval_seconds=1, stream=output, sleep=lambda _: None)
    tick = json.loads(output.getvalue().splitlines()[-1])
    assert tick["join_reconciliation"]["status"] == "DEGRADED"
    assert tick["dispatch_performed"] is False and tick["delivery_claim"] == "NONE"
