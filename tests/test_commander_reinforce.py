"""Tests for the reinforcement (fire-musket volley) orchestration in commander.

The reinforcement path only touches the local inbox/state files; it does not
invoke PowerShell or any desktop adapter, so it is safe to exercise headless.
"""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_evolution_protocol.coordination import commander as C


@pytest.fixture
def root(tmp_path):
    return tmp_path


def _write_order(r, order):
    C._atomic(C._root(r) / "inbox" / f"{order['id']}.json", order)


def test_parse_candidates():
    assert C._parse_candidates("beta, gamma") == ["beta", "gamma"]
    assert C._parse_candidates("beta gamma") == ["beta", "gamma"]
    assert C._parse_candidates("") == []
    assert C._parse_candidates("!!!@#") == []


def test_pick_reinforcement_candidate():
    order = {"id": "o1", "target": "alpha", "candidates": ["beta", "gamma"]}
    assert C._pick_reinforcement_candidate(order, []) == "beta"
    used = [{"parent": "o1", "target": "beta"}]
    assert C._pick_reinforcement_candidate(order, used) == "gamma"
    used.append({"parent": "o1", "target": "gamma"})
    assert C._pick_reinforcement_candidate(order, used) is None
    # the primary target is never re-picked as its own reinforcement
    solo = {"id": "o2", "target": "alpha", "candidates": ["alpha"]}
    assert C._pick_reinforcement_candidate(solo, []) is None


def test_reinforce_ceased_spawns_child_then_exhausts(root):
    order = C.submit_order(root, target="alpha", task="T1", text="do X",
                           sender="commander", candidates="beta, gamma")
    order["state"] = "CEASED"
    _write_order(root, order)
    c1 = C.reinforce(root, order["id"])
    assert c1 is not None and c1["target"] == "beta" and c1["parent"] == order["id"]
    c2 = C.reinforce(root, order["id"])
    assert c2 is not None and c2["target"] == "gamma"
    # candidates exhausted -> no third wave
    assert C.reinforce(root, order["id"]) is None


def test_reinforce_not_triggered_before_timeout(root):
    order = C.submit_order(root, target="alpha", task="T1", text="do X",
                           sender="commander", candidates="beta")
    order["state"] = "MESSAGE_VISIBLE"
    _write_order(root, order)
    assert C.reinforce(root, order["id"]) is None


def test_reinforce_skips_acked(root):
    order = C.submit_order(root, target="alpha", task="T1", text="do X",
                           sender="commander", candidates="beta")
    order["state"] = "AGENT_ACK"
    _write_order(root, order)
    assert C.reinforce(root, order["id"]) is None
