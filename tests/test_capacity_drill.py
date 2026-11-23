"""Guards for the quota-fault-injection drill.

The drill is a *simulation* with the real handoff state machine. These tests
lock down that it stays isolated, fail-closed, idempotent and honest about
what it does not prove.
"""
from pathlib import Path

from agent_evolution_protocol.federation import capacity_drill, command_handoff


def test_drill_passes_all_hard_properties(tmp_path):
    result = capacity_drill.run(tmp_path / "coord")
    assert result["verdict"] == "PASS", result
    assert all(result["checks"].values()), result["checks"]
    assert result["final_holder"] == "successor_gamma"
    assert result["chain_valid"] is True
    assert result["event_count"] == 5  # claim, 2x volunteer, yield, takeover; replays add none


def test_drill_never_touches_live_coordination(tmp_path):
    live = tmp_path / "coordination"
    live.mkdir()
    marker = live / command_handoff.STATE_FILE
    marker.parent.mkdir(parents=True)
    marker.write_text("UNTOUCHED", encoding="utf-8")
    capacity_drill.run(tmp_path / "isolated")
    assert marker.read_text(encoding="utf-8") == "UNTOUCHED"


def test_drill_result_is_deterministically_replayable(tmp_path):
    a = capacity_drill.run(tmp_path / "first")
    b = capacity_drill.run(tmp_path / "second")
    assert a["checks"] == b["checks"]
    assert a["handoff_id"] == b["handoff_id"]
    assert a["event_count"] == b["event_count"]


def test_drill_does_not_claim_real_ack_or_delivery(tmp_path):
    result = capacity_drill.run(tmp_path / "coord")
    note = result["note"].lower()
    assert "not a real agent ack" in note
    assert "not a desktop delivery" in note


def test_weak_successor_cannot_take_over(tmp_path):
    """Fail-closed: the ineligible candidate never becomes ACTIVE, even after yield."""
    from agent_evolution_protocol.federation import capability_bridge
    coord = tmp_path / "coord"
    COMMANDER = "commander"
    capability_bridge.eligibility(COMMANDER, ["desktop-ui"])
    command_handoff.claim(coord, agent="a", role=COMMANDER, capability="command",
                          evidence=["drill:claim"])
    weak = capability_bridge.eligibility(COMMANDER, ["research"])
    command_handoff.volunteer(coord, from_agent="weak", role=COMMANDER,
                              capability=weak["capability"], reason="help",
                              evidence=["drill:weak"])
    command_handoff.yield_role(coord, from_agent="a", role=COMMANDER,
                               signal="quota_exhausted", evidence=["drill:yield"])
    try:
        command_handoff.takeover(coord, to_agent="weak", role=COMMANDER,
                                 evidence=["drill:bad-takeover"])
    except ValueError:
        pass
    else:
        raise AssertionError("ineligible candidate must not take over")
    status = command_handoff.status(coord)
    assert status["roles"][COMMANDER]["state"] == command_handoff.YIELDED
    assert status["roles"][COMMANDER]["holder"] == "a"
