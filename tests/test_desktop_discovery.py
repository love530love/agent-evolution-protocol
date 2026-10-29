from agent_evolution_protocol.coordination.desktop_discovery import KNOWN
from agent_evolution_protocol.coordination.client_registry import BUILTIN, matches


def test_known_hosts_have_distinct_identities():
    assert KNOWN["Qoder"] != KNOWN["Qoder CN"]
    assert KNOWN["WorkBuddy"] != KNOWN["WorkBuddyAI"]


def test_qoder_and_qoder_cn_never_cross_match():
    assert "qoder_cn" in BUILTIN
    assert matches(BUILTIN["qoder"], {"title": "Qoder"})
    assert matches(BUILTIN["qoder_cn"], {"title": "Qoder CN"})
    assert not matches(BUILTIN["qoder"], {"title": "Qoder CN"})
    assert not matches(BUILTIN["qoder_cn"], {"title": "Qoder"})
