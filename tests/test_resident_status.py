from datetime import datetime, timedelta, timezone
import json

import pytest

from agent_evolution_protocol.coordination import resident_status


@pytest.fixture(autouse=True)
def _verified_process(monkeypatch):
    monkeypatch.setattr(resident_status.process_identity, "alive", lambda pid: True)
    monkeypatch.setattr(resident_status.process_identity, "verify_precise", lambda pid, started: True)


def _write(root, filename, payload, *, identity=True):
    if identity:
        payload = {"pid": 1234, "process_started_at": "2026-11-15T00:00:00+00:00", **payload}
    path = root / "runtime" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_status_requires_live_core_and_desktop_but_not_one_shot_elevated(tmp_path):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    payload = {"status": "CYCLE_COMPLETED", "completed_at": now.isoformat()}
    _write(tmp_path, "aep-core-host.heartbeat.json", payload)
    _write(tmp_path, "aep-supervisor.heartbeat.json", {
        "status": "TICK_COMPLETED", "completed_at": now.isoformat(),
        "mode": "dispatch", "dispatch_enabled": True,
    })
    result = resident_status.status(tmp_path, now=now)
    assert result["control_plane"] == "READY"
    assert result["desktop_mode"] == "dispatch"
    assert result["planes"]["elevated"]["status"] == "MISSING"
    assert result["delivery_claim"] == "NONE"


def test_status_holds_when_desktop_heartbeat_is_missing_or_stale(tmp_path):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    _write(tmp_path, "aep-core-host.heartbeat.json", {"status": "CYCLE_COMPLETED", "completed_at": now.isoformat()})
    result = resident_status.status(tmp_path, now=now)
    assert result["control_plane"] == "HOLD"
    assert result["planes"]["desktop"]["status"] == "MISSING"
    _write(tmp_path, "aep-supervisor.heartbeat.json", {
        "status": "TICK_COMPLETED", "completed_at": (now - timedelta(minutes=3)).isoformat(),
        "mode": "dispatch", "dispatch_enabled": True,
    })
    result = resident_status.status(tmp_path, now=now, max_age_seconds=90)
    assert result["planes"]["desktop"]["status"] == "STALE_OR_FAILED"
    assert result["desktop_mode"] == "UNVERIFIED"


def test_status_reports_observe_only_without_claiming_dispatch_readiness(tmp_path):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    _write(tmp_path, "aep-core-host.heartbeat.json", {"status": "CYCLE_COMPLETED", "completed_at": now.isoformat()})
    _write(tmp_path, "aep-supervisor.heartbeat.json", {
        "status": "TICK_COMPLETED", "completed_at": now.isoformat(),
        "mode": "observe-only", "dispatch_enabled": False,
    })
    result = resident_status.status(tmp_path, now=now)
    assert result["control_plane"] == "OBSERVE_ONLY"
    assert result["desktop_mode"] == "observe-only"


@pytest.mark.parametrize("mode, dispatch_enabled", [
    (None, None), ("", False), ("unknown", False),
    ("dispatch", None), ("dispatch", False),
    ("observe-only", None), ("observe-only", True),
])
def test_status_holds_when_desktop_mode_or_capability_is_missing_or_contradictory(
        tmp_path, mode, dispatch_enabled):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    _write(tmp_path, "aep-core-host.heartbeat.json", {"status": "CYCLE_COMPLETED", "completed_at": now.isoformat()})
    payload = {"status": "TICK_COMPLETED", "completed_at": now.isoformat()}
    if mode is not None:
        payload["mode"] = mode
    if dispatch_enabled is not None:
        payload["dispatch_enabled"] = dispatch_enabled
    _write(tmp_path, "aep-supervisor.heartbeat.json", payload)
    result = resident_status.status(tmp_path, now=now)
    assert result["control_plane"] == "HOLD"
    assert result["desktop_mode"] == "UNVERIFIED"


def test_failed_desktop_heartbeat_never_exposes_a_dispatch_mode(tmp_path):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    _write(tmp_path, "aep-core-host.heartbeat.json", {
        "status": "CYCLE_COMPLETED", "completed_at": now.isoformat(),
    })
    _write(tmp_path, "aep-supervisor.heartbeat.json", {
        "status": "TICK_FAILED", "completed_at": now.isoformat(),
        "mode": "dispatch", "dispatch_enabled": True,
    })
    result = resident_status.status(tmp_path, now=now)
    assert result["planes"]["desktop"]["status"] == "STALE_OR_FAILED"
    assert result["desktop_mode"] == "UNVERIFIED"
    assert result["control_plane"] == "HOLD"


def test_future_heartbeat_cannot_keep_a_dead_host_healthy(tmp_path):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    _write(tmp_path, "aep-core-host.heartbeat.json",
           {"status": "CYCLE_COMPLETED", "completed_at": (now + timedelta(hours=1)).isoformat()})
    _write(tmp_path, "aep-supervisor.heartbeat.json",
           {"status": "TICK_COMPLETED", "completed_at": now.isoformat()})
    result = resident_status.status(tmp_path, now=now)
    assert result["control_plane"] == "HOLD"
    assert result["planes"]["core"]["reason"] == "HEARTBEAT_TIMESTAMP_IN_FUTURE"


@pytest.mark.parametrize("age", [float("inf"), float("nan"), True, 0])
def test_status_rejects_invalid_heartbeat_age_threshold(tmp_path, age):
    with pytest.raises(ValueError, match="finite and positive"):
        resident_status.status(tmp_path, max_age_seconds=age)


@pytest.mark.parametrize("alive,verified,expected", [
    (False, None, "DEAD_PROCESS"),
    (None, None, "PROCESS_UNKNOWN"),
    (True, None, "PROCESS_UNKNOWN"),
    (True, False, "PID_MISMATCH"),
])
def test_fresh_heartbeat_does_not_prove_process_identity(tmp_path, monkeypatch, alive, verified, expected):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    _write(tmp_path, "aep-core-host.heartbeat.json", {"status": "CYCLE_COMPLETED", "completed_at": now.isoformat()})
    _write(tmp_path, "aep-supervisor.heartbeat.json", {"status": "TICK_COMPLETED", "completed_at": now.isoformat(),
            "mode": "dispatch", "dispatch_enabled": True})
    monkeypatch.setattr(resident_status.process_identity, "alive", lambda pid: alive)
    monkeypatch.setattr(resident_status.process_identity, "verify_precise", lambda pid, started: verified)
    result = resident_status.status(tmp_path, now=now)
    assert result["planes"]["core"]["heartbeat_fresh"] is True
    assert result["planes"]["core"]["status"] == expected
    assert result["planes"]["core"]["healthy"] is False
    assert result["control_plane"] == "HOLD"


def test_legacy_heartbeat_missing_process_token_is_unknown(tmp_path):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    _write(tmp_path, "aep-core-host.heartbeat.json", {"pid": 1234, "status": "CYCLE_COMPLETED",
           "completed_at": now.isoformat()}, identity=False)
    result = resident_status.status(tmp_path, now=now)
    assert result["planes"]["core"]["heartbeat_fresh"] is True
    assert result["planes"]["core"]["pid_alive"] is True
    assert result["planes"]["core"]["pid_identity"] == "UNKNOWN"
    assert result["planes"]["core"]["status"] == "PROCESS_UNKNOWN"
    assert result["control_plane"] == "HOLD"


def test_legacy_heartbeat_missing_pid_is_unknown(tmp_path, monkeypatch):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    monkeypatch.setattr(resident_status.process_identity, "alive", lambda pid: None)
    _write(tmp_path, "aep-core-host.heartbeat.json", {"status": "CYCLE_COMPLETED",
           "completed_at": now.isoformat()}, identity=False)
    result = resident_status.status(tmp_path, now=now)
    assert result["planes"]["core"]["heartbeat_fresh"] is True
    assert result["planes"]["core"]["pid_alive"] is None
    assert result["planes"]["core"]["pid_identity"] == "UNKNOWN"
    assert result["planes"]["core"]["status"] == "PROCESS_UNKNOWN"
    assert result["control_plane"] == "HOLD"


def test_stale_heartbeat_cannot_be_healthy_with_verified_pid(tmp_path):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    _write(tmp_path, "aep-core-host.heartbeat.json", {"status": "CYCLE_COMPLETED",
           "completed_at": (now - timedelta(minutes=5)).isoformat()})
    result = resident_status.status(tmp_path, now=now)
    assert result["planes"]["core"]["heartbeat_fresh"] is False
    assert result["planes"]["core"]["pid_identity"] == "VERIFIED"
    assert result["planes"]["core"]["healthy"] is False


def test_heartbeat_written_before_process_start_is_not_healthy(tmp_path):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    _write(tmp_path, "aep-core-host.heartbeat.json", {"status": "CYCLE_COMPLETED",
           "completed_at": now.isoformat(), "process_started_at": (now + timedelta(seconds=1)).isoformat()})
    result = resident_status.status(tmp_path, now=now + timedelta(seconds=2))
    assert result["planes"]["core"]["heartbeat_fresh"] is True
    assert result["planes"]["core"]["pid_identity"] == "MISMATCH"
    assert result["planes"]["core"]["status"] == "PID_MISMATCH"
