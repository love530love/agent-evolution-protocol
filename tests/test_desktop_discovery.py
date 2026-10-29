from agent_evolution_protocol.coordination.desktop_discovery import KNOWN


def test_known_hosts_have_distinct_identities():
    assert KNOWN["Qoder"] != KNOWN["Qoder CN"]
    assert KNOWN["WorkBuddy"] != KNOWN["WorkBuddyAI"]
