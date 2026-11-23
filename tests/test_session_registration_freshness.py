from datetime import datetime, timedelta, timezone
import json

import pytest

from agent_evolution_protocol.coordination import session_registry as registry
from agent_evolution_protocol.coordination import wake_router


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def registered(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_now", lambda: NOW.isoformat())
    registry.register(tmp_path, agent="qoder", workspace=str(tmp_path), provider="uia", session_id="s1")
    return tmp_path / "clients" / "sessions" / "qoder__s1.json"


def change(path, **fields):
    row = json.loads(path.read_text(encoding="utf-8"))
    for key, value in fields.items():
        if value is None:
            row.pop(key, None)
        else:
            row[key] = value
    path.write_text(json.dumps(row), encoding="utf-8")


def test_registration_freshness_boundary_and_explicit_window(tmp_path, monkeypatch):
    registered(tmp_path, monkeypatch)
    fresh = registry.resolve(tmp_path, agent="qoder", now=NOW + timedelta(seconds=30), max_age_seconds=30)
    assert fresh["ok"] is True
    assert fresh["session_observation_verified"] is False
    stale = registry.resolve(tmp_path, agent="qoder", now=NOW + timedelta(seconds=31), max_age_seconds=30)
    assert stale["reason"] == "SESSION_REGISTRATION_STALE"


@pytest.mark.parametrize("stamp", [None, "invalid", "2026-01-01T00:00:00", 17])
def test_missing_invalid_or_naive_registration_is_unknown(tmp_path, monkeypatch, stamp):
    path = registered(tmp_path, monkeypatch)
    change(path, registration_refreshed_at=stamp)
    assert registry.resolve(tmp_path, agent="qoder", now=NOW)["reason"] == "SESSION_REGISTRATION_TIME_UNKNOWN"


def test_future_registration_is_refused(tmp_path, monkeypatch):
    path = registered(tmp_path, monkeypatch)
    change(path, registration_refreshed_at=(NOW + timedelta(seconds=1)).isoformat())
    assert registry.resolve(tmp_path, agent="qoder", now=NOW)["reason"] == "SESSION_REGISTRATION_IN_FUTURE"


def test_state_transition_does_not_freshen_old_registration(tmp_path, monkeypatch):
    path = registered(tmp_path, monkeypatch)
    monkeypatch.setattr(registry, "_now", lambda: (NOW + timedelta(hours=2)).isoformat())
    registry.transition(tmp_path, agent="qoder", session_id="s1", state="delivered", event_id="e1")
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["registration_refreshed_at"] == NOW.isoformat()
    assert registry.resolve(tmp_path, agent="qoder", now=NOW + timedelta(hours=2))["reason"] == "SESSION_REGISTRATION_STALE"


def test_legacy_can_be_reregistered_without_changing_first_registration(tmp_path, monkeypatch):
    path = registered(tmp_path, monkeypatch)
    change(path, registration_refreshed_at=None)
    assert registry.resolve(tmp_path, agent="qoder", now=NOW)["reason"] == "SESSION_REGISTRATION_TIME_UNKNOWN"
    monkeypatch.setattr(registry, "_now", lambda: (NOW + timedelta(hours=1)).isoformat())
    row = registry.register(tmp_path, agent="qoder", workspace=str(tmp_path), provider="uia", session_id="s1")
    assert row["registered_at"] == NOW.isoformat()
    assert registry.resolve(tmp_path, agent="qoder", now=NOW + timedelta(hours=1))["ok"] is True


@pytest.mark.parametrize("age", [0, -1, float("nan"), float("inf"), True, "900"])
def test_invalid_max_age_is_rejected(tmp_path, age):
    with pytest.raises(ValueError, match="finite positive"):
        registry.resolve(tmp_path, agent="qoder", max_age_seconds=age)


def test_wake_router_never_calls_transport_for_legacy_timestamp(tmp_path, monkeypatch):
    path = registered(tmp_path, monkeypatch)
    change(path, registration_refreshed_at=None)
    calls = []
    result = wake_router.route(tmp_path, agent="qoder", text="x", event_id="event-1", execute=True,
                               transports=[wake_router.Transport("uia", lambda payload: calls.append(payload))])
    assert result["status"] == "SESSION_REGISTRATION_TIME_UNKNOWN"
    assert calls == []


def test_wake_router_never_calls_transport_for_stale_registration(tmp_path, monkeypatch):
    registered(tmp_path, monkeypatch)
    path = tmp_path / "clients" / "sessions" / "qoder__s1.json"
    change(path, registration_refreshed_at="2000-01-01T00:00:00+00:00")
    calls = []
    result = wake_router.route(tmp_path, agent="qoder", text="x", event_id="event-2", execute=True,
                               transports=[wake_router.Transport("uia", lambda payload: calls.append(payload))])
    assert result["status"] == "SESSION_REGISTRATION_STALE"
    assert calls == []


def test_stale_competing_candidate_does_not_prove_unique_target(tmp_path, monkeypatch):
    path = registered(tmp_path, monkeypatch)
    change(path, registration_refreshed_at="2000-01-01T00:00:00+00:00")
    registry.register(tmp_path, agent="qoder", workspace=str(tmp_path), provider="uia", session_id="s2")
    assert registry.resolve(tmp_path, agent="qoder", now=NOW)["reason"] == "AMBIGUOUS_SESSION"
