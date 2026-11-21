from datetime import datetime, timedelta, timezone
import json

import pytest

from agent_evolution_protocol.coordination import resident_status


def _write(root, filename, payload):
    path = root / "runtime" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_status_requires_live_core_and_desktop_but_not_one_shot_elevated(tmp_path):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    payload = {"status": "CYCLE_COMPLETED", "completed_at": now.isoformat()}
    _write(tmp_path, "aep-core-host.heartbeat.json", payload)
    _write(tmp_path, "aep-supervisor.heartbeat.json", {"status": "TICK_COMPLETED", "completed_at": now.isoformat()})
    result = resident_status.status(tmp_path, now=now)
    assert result["control_plane"] == "READY"
    assert result["planes"]["elevated"]["status"] == "MISSING"
    assert result["delivery_claim"] == "NONE"


def test_status_holds_when_desktop_heartbeat_is_missing_or_stale(tmp_path):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    _write(tmp_path, "aep-core-host.heartbeat.json", {"status": "CYCLE_COMPLETED", "completed_at": now.isoformat()})
    result = resident_status.status(tmp_path, now=now)
    assert result["control_plane"] == "HOLD"
    assert result["planes"]["desktop"]["status"] == "MISSING"
    _write(tmp_path, "aep-supervisor.heartbeat.json", {"status": "TICK_COMPLETED", "completed_at": (now - timedelta(minutes=3)).isoformat()})
    result = resident_status.status(tmp_path, now=now, max_age_seconds=90)
    assert result["planes"]["desktop"]["status"] == "STALE_OR_FAILED"


def test_status_reports_observe_only_without_claiming_dispatch_readiness(tmp_path):
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    _write(tmp_path, "aep-core-host.heartbeat.json", {"status": "CYCLE_COMPLETED", "completed_at": now.isoformat()})
    _write(tmp_path, "aep-supervisor.heartbeat.json", {"status": "TICK_COMPLETED", "completed_at": now.isoformat(), "mode": "observe-only"})
    result = resident_status.status(tmp_path, now=now)
    assert result["control_plane"] == "OBSERVE_ONLY"


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
