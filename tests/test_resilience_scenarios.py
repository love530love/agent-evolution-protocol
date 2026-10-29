from pathlib import Path
from agent_evolution_protocol.coordination.resilience_scenarios import run


def test_ack_timeout_routes_to_bounded_recovery(tmp_path: Path):
    out = run(tmp_path, "ack_timeout")
    assert out["ack"]["state"] == "ack_timeout"
    assert out["recovery"]["action"] == "FAILOVER"


def test_restart_recovers_persisted_mission(tmp_path: Path):
    out = run(tmp_path, "restart_recovery")
    assert out["recovered"] is True


def test_all_agents_offline_is_observable(tmp_path: Path):
    out = run(tmp_path, "all_agents_offline")
    assert out["supervisor"]["missions"] == 1
