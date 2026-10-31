from agent_evolution_protocol.coordination.desktop_discovery import KNOWN
from agent_evolution_protocol.coordination.client_registry import BUILTIN, matches
import json
from pathlib import Path

import pytest


def _live_anchor(name):
    """Return a machine-local anchor, or skip.

    ``coordination/`` is a live workspace directory and is deliberately not in
    the repository, so these assertions describe the calibration of the machine
    that produced the anchor.  On a clean checkout there is nothing to assert
    about, and failing the whole job for that would be a lie about the code.
    """
    path = Path(__file__).parents[1] / "coordination" / "clients" / "anchors" / f"{name}.json"
    if not path.is_file():
        pytest.skip(f"{path.name} is a live workspace anchor, absent on a clean checkout")
    return json.loads(path.read_text(encoding="utf-8"))


def test_known_hosts_have_distinct_identities():
    assert KNOWN["Qoder"] != KNOWN["Qoder CN"]
    assert KNOWN["WorkBuddy"] != KNOWN["WorkBuddyAI"]


def test_qoder_and_qoder_cn_never_cross_match():
    assert "qoder_cn" in BUILTIN
    assert matches(BUILTIN["qoder"], {"title": "Qoder"})
    assert matches(BUILTIN["qoder_cn"], {"title": "Qoder CN"})
    assert not matches(BUILTIN["qoder"], {"title": "Qoder CN"})
    assert not matches(BUILTIN["qoder_cn"], {"title": "Qoder"})


def test_persisted_qoder_anchor_is_international_client():
    anchor = _live_anchor("qoder")
    assert anchor["agent"] == "qoder"
    assert anchor["title"] == "Qoder"
    assert anchor["pid"] != _live_anchor("qoder_cn")["pid"]
