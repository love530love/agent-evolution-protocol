import pytest
from agent_evolution_protocol.coordination.provider_readback import receipt, accepted

def test_ack_receipt_is_accepted():
    value = receipt(provider="uia", event_id="evt-1", state="AGENT_ACK", observed=True, ack=True, evidence=["shot.png"])
    assert accepted(value)

def test_visible_is_not_ack():
    value = receipt(provider="cdp", event_id="evt-2", state="MESSAGE_VISIBLE", observed=True)
    assert not accepted(value)

def test_ack_requires_explicit_token():
    with pytest.raises(ValueError): receipt(provider="mcp", event_id="evt-3", state="AGENT_ACK", observed=True)
