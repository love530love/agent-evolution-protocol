"""collab_health: evidence-graded, read-only collaboration snapshot."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_evolution_protocol.coordination import collab_health
from agent_evolution_protocol.coordination.collab_health import main, snapshot

NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)


def put(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def presence(root: Path, agent: str, *, state: str, expires_at: datetime | None) -> None:
    put(root / "presence" / "agents" / f"{agent}.json", {
        "agent": agent, "state": state, "source": "test",
        "expires_at": expires_at.isoformat() if expires_at else "",
    })


def heartbeat(root: Path, filename: str, *, status: str, tick: int, at: datetime,
              pid: int | None = None, extra: dict | None = None) -> None:
    payload = {
        "schema": "aep-heartbeat-test", "status": status, "tick": tick,
        "completed_at": at.isoformat(),
        "pid": os.getpid() if pid is None else pid,
    }
    payload.update(extra or {})
    put(root / "runtime" / filename, payload)


def healthy(root: Path, *, at: datetime = NOW) -> None:
    presence(root, "qoder", state="online", expires_at=at + timedelta(minutes=10))
    heartbeat(root, "aep-core-host.heartbeat.json", status="CYCLE_COMPLETED", tick=5, at=at)
    heartbeat(root, "aep-supervisor.heartbeat.json", status="TICK_COMPLETED", tick=7, at=at)


def fingerprint(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_empty_root_has_no_online_agent_and_no_invented_layers():
    with TemporaryDirectory() as folder:
        result = snapshot(Path(folder), now=NOW)
        assert result["verdict"] == "NO_ONLINE_AGENT"
        assert result["online_layers"]["file_registered_online"] == 0
        assert result["online_layers"]["presence_effective_fresh"] == 0
        assert result["hosts"]["core"]["fresh"] == "unknown"
        assert result["hosts"]["desktop"]["fresh"] == "unknown"


def test_stale_evidence_is_not_reported_as_offline():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        presence(root, "codex", state="online", expires_at=NOW - timedelta(seconds=1))
        result = snapshot(root, now=NOW)
        layers = result["online_layers"]
        assert layers["file_registered_online"] == 1
        assert layers["presence_effective_fresh"] == 0
        assert layers["presence_stale"] == 1
        assert result["verdict"] == "NO_ONLINE_AGENT"
        assert "offline" not in json.dumps(result, ensure_ascii=False)


def test_message_visible_and_mailbox_read_are_not_agent_ack():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        put(root / "messages" / "qoder" / "m1.json",
            {"id": "m1", "to": "qoder", "topic": "T1", "kind": "warning"})
        put(root / "acks" / "qoder" / "m1.json", {"id": "m1", "agent": "qoder"})
        put(root / "commander" / "state" / "o1.json",
            {"id": "o1", "target": "qoder", "event_id": "m1", "state": "MESSAGE_VISIBLE"})
        delivery = snapshot(root, now=NOW)["delivery"]
        assert delivery["message_visible"] == 1
        assert delivery["agent_accepted"] == 0
        assert delivery["next_required"] == {"AGENT_ACK": 1}


def test_unreliable_layers_and_heartbeat_pid_are_unknown():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        presence(root, "codex", state="online", expires_at=NOW + timedelta(minutes=10))
        heartbeat(root, "aep-core-host.heartbeat.json", status="CYCLE_COMPLETED", tick=3, at=NOW)
        put(root / "runtime" / "aep-supervisor.heartbeat.json", {"pid": 9, "tick": 4})
        result = snapshot(root, now=NOW)
        layers = result["online_layers"]
        assert layers["os_processes_running"] == "unknown"
        assert layers["windows_actionable"] == "unknown"
        assert result["hosts"]["desktop"]["tick"] == 4
        assert result["hosts"]["desktop"]["at"] == "unknown"
        assert result["hosts"]["desktop"]["fresh"] == "unknown"
        assert result["verdict"] == "HOSTS_UNKNOWN"


def test_dead_pid_is_not_reported_as_fresh_host():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        presence(root, "qoder", state="online", expires_at=NOW + timedelta(minutes=10))
        # Unused PID: heartbeat file is fresh, but the process is provably gone.
        heartbeat(root, "aep-core-host.heartbeat.json", status="CYCLE_COMPLETED",
                  tick=5, at=NOW, pid=0x7FFFFFFE)
        heartbeat(root, "aep-supervisor.heartbeat.json", status="TICK_COMPLETED",
                  tick=7, at=NOW)
        result = snapshot(root, now=NOW)
        core = result["hosts"]["core"]
        assert core["health"] == "DEAD_PROCESS"
        assert core["fresh"] is False
        assert core["pid_alive"] is False
        assert result["verdict"] == "HOSTS_STALE"


def test_posix_pid_probe_distinguishes_missing_from_access_denied(monkeypatch):
    import pytest
    if os.name != "posix":
        pytest.skip("POSIX process liveness semantics")

    calls = []
    def probe(pid, signal):
        calls.append((pid, signal))
        if pid == 11:
            raise ProcessLookupError()
        if pid == 12:
            raise PermissionError()
    monkeypatch.setattr(collab_health.os, "kill", probe)
    assert collab_health._pid_alive(10) is True
    assert collab_health._pid_alive(11) is False
    assert collab_health._pid_alive(12) is None
    assert calls == [(10, 0), (11, 0), (12, 0)]


def test_reused_pid_is_not_reported_as_fresh_host(monkeypatch):
    with TemporaryDirectory() as folder:
        root = Path(folder)
        presence(root, "qoder", state="online", expires_at=NOW + timedelta(minutes=10))
        token = "2026-01-01T00:00:00+00:00"
        heartbeat(root, "aep-core-host.heartbeat.json", status="CYCLE_COMPLETED",
                  tick=5, at=NOW, extra={"process_started_at": token})
        heartbeat(root, "aep-supervisor.heartbeat.json", status="TICK_COMPLETED",
                  tick=7, at=NOW)
        monkeypatch.setattr(collab_health.process_identity, "verify", lambda pid, claim: False)
        result = snapshot(root, now=NOW)
        core = result["hosts"]["core"]
        assert core["health"] == "PID_MISMATCH"
        assert core["pid_identity"] == "MISMATCH"
        assert core["fresh"] is False
        assert result["verdict"] == "HOSTS_STALE"


def test_verified_process_identity_keeps_host_fresh(monkeypatch):
    with TemporaryDirectory() as folder:
        root = Path(folder)
        healthy(root)
        token = "2026-01-01T00:00:00+00:00"
        put(root / "runtime" / "aep-core-host.heartbeat.json", {
            "schema": "aep-heartbeat-test", "status": "CYCLE_COMPLETED", "tick": 5,
            "completed_at": NOW.isoformat(), "pid": os.getpid(),
            "process_started_at": token,
        })
        put(root / "runtime" / "aep-supervisor.heartbeat.json", {
            "schema": "aep-heartbeat-test", "status": "TICK_COMPLETED", "tick": 7,
            "completed_at": NOW.isoformat(), "pid": os.getpid(),
            "process_started_at": token,
        })
        monkeypatch.setattr(collab_health.process_identity, "verify", lambda pid, claim: True)
        result = snapshot(root, now=NOW)
        assert result["verdict"] == "READY"
        assert result["hosts"]["core"]["pid_identity"] == "VERIFIED"


def test_future_heartbeat_is_marked_clock_skew_not_fresh():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        presence(root, "qoder", state="online", expires_at=NOW + timedelta(minutes=10))
        heartbeat(root, "aep-core-host.heartbeat.json", status="CYCLE_COMPLETED",
                  tick=5, at=NOW + timedelta(minutes=5))
        heartbeat(root, "aep-supervisor.heartbeat.json", status="TICK_COMPLETED",
                  tick=7, at=NOW)
        result = snapshot(root, now=NOW)
        assert result["hosts"]["core"]["clock_skew_suspect"] is True
        assert result["hosts"]["core"]["health"] == "UNKNOWN"
        assert result["clock_skew_suspect"] is True


def test_verdict_is_deterministic_and_prioritized():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        healthy(root)
        first = snapshot(root, now=NOW)
        assert first == snapshot(root, now=NOW)
        assert first["verdict"] == "READY"
        assert first["hosts"]["core"]["tick"] == 5
        assert first["hosts"]["desktop"]["tick"] == 7
        later = snapshot(root, now=NOW + timedelta(seconds=200))
        assert later["verdict"] == "HOSTS_STALE"
        presence(root, "qoder", state="online", expires_at=NOW - timedelta(seconds=1))
        assert snapshot(root, now=NOW + timedelta(seconds=200))["verdict"] == "NO_ONLINE_AGENT"


def test_main_prints_parseable_json():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        # main() evaluates at the real clock, so build evidence relative to it.
        healthy(root, at=datetime.now(timezone.utc))
        output = StringIO()
        with redirect_stdout(output):
            code = main(["--root", str(root)])
        assert code == 0
        result = json.loads(output.getvalue())
        assert result["verdict"] == "READY"
        assert result["schema"] == "aep-collab-health-v1"


def test_snapshot_writes_nothing():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        healthy(root)
        put(root / "messages" / "qoder" / "m1.json", {"id": "m1", "to": "qoder"})
        before = fingerprint(root)
        snapshot(root, now=NOW)
        assert fingerprint(root) == before
