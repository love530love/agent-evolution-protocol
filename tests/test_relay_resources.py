from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from agent_evolution_protocol.relay import resources

NOW = datetime(2026, 11, 22, 6, 0, tzinfo=timezone.utc)
EVIDENCE = ["quota screenshot 2026-11-22T06:00Z"]


def declare(root, agent="agent-a", **overrides):
    values = dict(agent=agent, provider="vendor", unit="credits", remaining=10,
                  reserve=0, evidence=EVIDENCE, capabilities=["command"], now=NOW)
    values.update(overrides)
    return resources.declare(root, **values)


def test_declare_effective_and_expiry(tmp_path):
    row = declare(tmp_path, remaining=10, reserve=2, limit=20, ttl_minutes=60)
    assert row["state"] == "ACTIVE"
    assert row["available"] == 8
    expired = resources.effective(resources.load(tmp_path, "agent-a"),
                                 NOW + timedelta(minutes=61))
    assert expired["state"] == "EXPIRED"
    assert expired["available"] == 8


def test_gate_is_fail_closed_for_undeclared_agent(tmp_path):
    verdict = resources.gate(tmp_path, agent="agent-x", units=1, now=NOW)
    assert verdict["allowed"] is False
    assert "UNDECLARED" in verdict["reasons"]


def test_gate_enforces_subagent_budgets(tmp_path):
    declare(tmp_path, remaining=10, subagent_children=1, subagent_units=5)
    assert resources.gate(tmp_path, agent="agent-a", children=2,
                          now=NOW)["reasons"] == ["SUBAGENT_CHILD_BUDGET"]
    assert resources.gate(tmp_path, agent="agent-a", units=6,
                          now=NOW)["reasons"] == ["SUBAGENT_UNIT_BUDGET"]
    assert resources.gate(tmp_path, agent="agent-a", units=5, children=1,
                          now=NOW)["allowed"] is True


def test_exhausted_without_envelope_blocks_and_can_be_refreshed(tmp_path):
    row = resources.mark_exhausted(tmp_path, agent="agent-c", reason="provider said no",
                                   evidence=EVIDENCE, now=NOW)
    assert row["state"] == "BLOCKED"
    assert resources.gate(tmp_path, agent="agent-c", now=NOW)["allowed"] is False
    refreshed = declare(tmp_path, agent="agent-c", remaining=3)
    assert refreshed["state"] == "ACTIVE"
    assert resources.gate(tmp_path, agent="agent-c", now=NOW)["allowed"] is True


def test_reserve_is_not_delegable(tmp_path):
    row = declare(tmp_path, remaining=3, reserve=3)
    assert row["state"] == "EXHAUSTED"
    assert row["available"] == 0
    assert resources.gate(tmp_path, agent="agent-a", now=NOW)["allowed"] is False


def test_rank_prefers_capable_active_quota(tmp_path):
    declare(tmp_path, agent="agent-a", remaining=5)
    declare(tmp_path, agent="agent-b", remaining=50)
    declare(tmp_path, agent="agent-c", remaining=100,
            capabilities=["build"])
    rows = resources.rank(tmp_path, capability="command", now=NOW)
    assert [row["agent"] for row in rows] == ["agent-b", "agent-a", "agent-c"]
    assert rows[0]["capability_match"] is True
    assert rows[1]["capability_match"] is True
    assert rows[2]["capability_match"] is False
    assert rows[2]["eligible"] is False
    assert rows[0]["score"] > rows[1]["score"]


def test_rank_reports_exhausted_and_can_exclude(tmp_path):
    declare(tmp_path, agent="agent-a", remaining=1, reserve=1)
    declare(tmp_path, agent="agent-b", remaining=10)
    rows = resources.rank(tmp_path, capability="command", exclude=("agent-b",), now=NOW)
    assert [row["agent"] for row in rows] == ["agent-a"]
    assert rows[0]["eligible"] is False
    assert "EXHAUSTED" in rows[0]["reasons"]


@pytest.mark.parametrize("overrides", [
    {"unit": "bananas"},
    {"remaining": -1},
    {"remaining": 5, "limit": 4},
    {"ttl_minutes": 0},
    {"source": "guess"},
    {"unit": "percent", "remaining": 101, "limit": 100},
])
def test_declare_validation(tmp_path, overrides):
    with pytest.raises(ValueError):
        declare(tmp_path, **overrides)


def test_snapshot_reports_invalid_envelope(tmp_path):
    declare(tmp_path, remaining=10)
    folder = Path(tmp_path) / "relay" / "resources"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "broken.json").write_text(json.dumps({
        "schema": resources.SCHEMA, "agent": "broken", "provider": "vendor",
        "unit": "credits", "remaining": "many", "reserve": 0,
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
    }), encoding="utf-8")
    snapshot = resources.snapshot(tmp_path, at=NOW)
    assert [row["agent"] for row in snapshot["resources"]] == ["agent-a"]
    assert len(snapshot["invalid"]) == 1
    assert snapshot["invalid"][0]["reason"] == "INVALID_ENVELOPE"