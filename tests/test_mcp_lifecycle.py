from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_evolution_protocol.coordination import mcp_lifecycle as lifecycle


NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def _record(root: Path, *, pid: int = 101, moment: datetime = NOW) -> dict:
    return lifecycle.register(
        root, agent="codex", workspace_id="ws-a", server="aep-federation",
        transport="stdio", pid=pid, parent_pid=99,
        argv=["python", "-m", "agent_evolution_protocol.mcp_server"], moment=moment,
    )


def test_register_uses_immutable_run_ids_and_hides_command_contents():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        first = _record(root, pid=101)
        second = _record(root, pid=101)
        assert first["run_id"] != second["run_id"]
        assert lifecycle.path(root, first["run_id"]).is_file()
        assert lifecycle.path(root, second["run_id"]).is_file()
        raw = lifecycle.path(root, first["run_id"]).read_text(encoding="utf-8")
        assert "agent_evolution_protocol.mcp_server" not in raw
        assert first["state"] == "RUNNING"


def test_heartbeat_and_retire_require_the_exact_run_identity():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        record = _record(root)
        assert lifecycle.heartbeat(root, pid=102, run_id=record["run_id"], moment=NOW) is None
        assert lifecycle.heartbeat(root, pid=101, run_id="0" * 32, moment=NOW) is None
        updated = lifecycle.heartbeat(root, pid=101, run_id=record["run_id"], moment=NOW + timedelta(seconds=5))
        assert updated and updated["heartbeat_at"] == (NOW + timedelta(seconds=5)).isoformat()
        retired = lifecycle.retire(root, pid=101, run_id=record["run_id"], moment=NOW + timedelta(seconds=7))
        assert retired and retired["state"] == "EXITED"
        assert retired["stopped_at"] == (NOW + timedelta(seconds=7)).isoformat()
        assert lifecycle.heartbeat(root, pid=101, run_id=record["run_id"], moment=NOW + timedelta(seconds=8)) is None
        assert lifecycle.path(root, record["run_id"]).is_file()


def test_snapshot_separates_live_stale_dead_unknown_and_exited_records():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        live = _record(root, pid=11)
        stale = _record(root, pid=12, moment=NOW - timedelta(minutes=10))
        dead = _record(root, pid=13, moment=NOW - timedelta(minutes=10))
        unknown = _record(root, pid=14, moment=NOW - timedelta(minutes=10))
        exited = _record(root, pid=15, moment=NOW - timedelta(minutes=10))
        lifecycle.retire(root, pid=15, run_id=exited["run_id"], moment=NOW - timedelta(minutes=9))
        states = {11: "live", 12: "live", 13: "dead", 14: "unknown"}
        report = lifecycle.snapshot(root, probe=lambda pid: states[pid], moment=NOW, stale_after_seconds=60)
        by_pid = {row["pid"]: row for row in report["instances"] if "pid" in row}
        assert by_pid[live["pid"]]["status"] == "LIVE"
        assert by_pid[stale["pid"]]["status"] == "HEARTBEAT_STALE"
        assert by_pid[dead["pid"]]["status"] == "DEAD_PID"
        assert by_pid[unknown["pid"]]["status"] == "UNKNOWN_PID"
        assert by_pid[exited["pid"]]["status"] == "EXITED"
        assert report["processes_terminated"] == 0


def test_prune_retains_stale_unknown_and_recent_forensics_but_removes_old_dead_or_exited():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        stale = _record(root, pid=21, moment=NOW - timedelta(days=3))
        unknown = _record(root, pid=22, moment=NOW - timedelta(days=3))
        dead = _record(root, pid=23, moment=NOW - timedelta(days=3))
        exited = _record(root, pid=24, moment=NOW - timedelta(days=3))
        recent = _record(root, pid=25, moment=NOW)
        lifecycle.retire(root, pid=24, run_id=exited["run_id"], moment=NOW - timedelta(days=2))
        lifecycle.retire(root, pid=25, run_id=recent["run_id"], moment=NOW - timedelta(minutes=1))
        statuses = {21: "live", 22: "unknown", 23: "dead"}
        report = lifecycle.prune(root, probe=lambda pid: statuses[pid], moment=NOW,
                                 stale_after_seconds=60, retention_seconds=3600)
        removed = set(report["removed_records"])
        assert str(lifecycle.path(root, dead["run_id"])) in removed
        assert str(lifecycle.path(root, exited["run_id"])) in removed
        assert lifecycle.path(root, stale["run_id"]).is_file()
        assert lifecycle.path(root, unknown["run_id"]).is_file()
        assert lifecycle.path(root, recent["run_id"]).is_file()
        assert report["processes_terminated"] == 0


def test_invalid_record_is_visible_and_never_pruned_as_a_process():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        folder_path = lifecycle.directory(root)
        folder_path.mkdir(parents=True)
        invalid = folder_path / ("f" * 32 + ".json")
        invalid.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
        report = lifecycle.prune(root, probe=lambda _: "dead", moment=NOW, retention_seconds=1)
        assert report["counts"]["INVALID_RECORD"] == 1
        assert invalid.is_file()
        assert report["processes_terminated"] == 0
