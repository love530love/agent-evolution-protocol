from agent_evolution_protocol.coordination.desktop_discovery import KNOWN
from agent_evolution_protocol.coordination.client_registry import BUILTIN, matches
import json
from pathlib import Path


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
    root = Path(__file__).parents[1]
    anchor = json.loads((root / "coordination" / "clients" / "anchors" / "qoder.json").read_text(encoding="utf-8"))
    assert anchor["agent"] == "qoder"
    assert anchor["title"] == "Qoder"
    assert anchor["pid"] != json.loads(
        (root / "coordination" / "clients" / "anchors" / "qoder_cn.json").read_text(encoding="utf-8")
    )["pid"]
