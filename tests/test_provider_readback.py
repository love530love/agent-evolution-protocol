import pytest
from agent_evolution_protocol.coordination.provider_readback import receipt, accepted
from agent_evolution_protocol.coordination.commander import provider_readback

def test_ack_receipt_is_accepted():
    value = receipt(provider="uia", event_id="evt-1", state="AGENT_ACK", observed=True, ack=True, evidence=["shot.png"])
    assert accepted(value)

def test_visible_is_not_ack():
    value = receipt(provider="cdp", event_id="evt-2", state="MESSAGE_VISIBLE", observed=True)
    assert not accepted(value)

def test_ack_requires_explicit_token():
    with pytest.raises(ValueError): receipt(provider="mcp", event_id="evt-3", state="AGENT_ACK", observed=True)

def test_commander_normalizer_keeps_visibility_below_ack():
    value = provider_readback("cdp", "evt-4", {"task_delivered": True})
    assert value["state"] == "MESSAGE_VISIBLE"
    assert value["accepted"] is False
