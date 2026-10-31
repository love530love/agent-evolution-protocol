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


def test_a_receipt_must_earn_input_submitted():
    # The default used to be "sent": anything not on a hand-written list of
    # failure statuses became INPUT_SUBMITTED, so every status nobody had
    # taught the normalizer about was recorded as proof that input was entered.
    # Measured before the fix, all four of these came back INPUT_SUBMITTED.
    for raw in ({"status": "urgent-interject-unavailable", "interruption": True},
                {"status": "composer-not-confirmed"},
                {"status": "composer-confirmed-no-send"},
                {"status": "LAUNCH_FORBIDDEN", "error": "no window for this agent"}):
        value = provider_readback("uia", "evt-5", raw)
        assert value["state"] == "FAILED", raw
        assert value["observed"] is False
    assert provider_readback("uia", "evt-6", {"status": "LAUNCH_FORBIDDEN",
                                              "error": "no window"})["error"] == "no window"


def test_positive_proof_of_input_still_counts():
    for raw in ({"input_verified": True}, {"composer_verified": True},
                {"status": "submitted-unconfirmed"}):
        assert provider_readback("uia", "evt-7", raw)["state"] == "INPUT_SUBMITTED", raw


def test_a_failure_keeps_the_status_so_the_reason_survives():
    value = provider_readback("uia", "evt-8", {"status": "EXECUTOR_TOO_LOW",
                                               "drivable": False})
    assert value["state"] == "FAILED"
    assert value["error"] == "EXECUTOR_TOO_LOW"
    assert value["raw"]["drivable"] is False
