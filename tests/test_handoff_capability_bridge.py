"""Tests for the role_contract <-> command_handoff capability bridge."""
import json

import pytest

from agent_evolution_protocol.federation import capability_bridge as bridge
from agent_evolution_protocol.federation import command_handoff


def test_describe_covers_all_four_competition_roles():
    data = bridge.describe()
    assert data["schema"] == bridge.SCHEMA
    by_role = {row["role"]: row for row in data["roles"]}
    assert set(by_role) == {"commander", "scout", "github", "builder"}
    assert by_role["commander"]["handoff_capability"] == "command"
    assert by_role["github"]["aliases"] == ["git-access"]


def test_eligibility_maps_each_competition_role():
    assert bridge.eligibility("commander", ["desktop-ui", "cli"])["eligible"] is True
    scout = bridge.eligibility("scout", ["research", "cli"])
    assert scout["eligible"] is True
    assert scout["capability"] == "recon"
    assert bridge.eligibility("github", ["git-access"])["eligible"] is True
    assert bridge.eligibility("builder", ["packaging", "cli"])["eligible"] is True


def test_weak_agent_without_mapped_capability_is_rejected():
    verdict = bridge.eligibility("commander", ["cli"])
    assert verdict["eligible"] is False
    assert verdict["reason"] == "MISSING_REQUIRED_CAPABILITY"
    assert verdict["capability"] == ""


def test_native_handoff_token_is_accepted():
    verdict = bridge.eligibility("builder", ["build"])
    assert verdict["eligible"] is True
    assert verdict["mapped_from"] == ["build"]


def test_unknown_tokens_are_ignored_not_guessed():
    verdict = bridge.eligibility("github", ["unknown-skill", "git-access"])
    assert verdict["ignored"] == ["unknown-skill"]
    assert verdict["eligible"] is True


@pytest.mark.parametrize("bad", [None, "", "-leading", "BadRole", 42, "unknown_role"])
def test_invalid_role_fails_closed(bad):
    with pytest.raises(ValueError):
        bridge.eligibility(bad, ["cli"])


def test_invalid_capability_token_fails_closed():
    with pytest.raises(ValueError):
        bridge.eligibility("commander", ["bad token"])


def test_derived_capability_satisfies_command_handoff_covers():
    for role, capability in command_handoff.DEFAULT_ROLES.items():
        declared = [token for token, target in bridge.CAPABILITY_ALIASES.items()
                    if target == capability]
        verdict = bridge.eligibility(role, declared)
        assert verdict["eligible"] is True
        assert command_handoff._covers(verdict["capability"], capability)


def test_output_is_recomputable_byte_identical():
    first = json.dumps(bridge.eligibility("scout", ["research", "cli"]),
                       ensure_ascii=False, sort_keys=True)
    second = json.dumps(bridge.eligibility("scout", ["research", "cli"]),
                        ensure_ascii=False, sort_keys=True)
    assert first == second
