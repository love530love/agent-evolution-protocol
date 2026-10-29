from agent_evolution_protocol.coordination.recovery_policy import decide


def test_retryable_failure_fails_over_once():
    assert decide(status="TRANSPORT_ERROR", attempts=0)["action"] == "FAILOVER"


def test_ambiguous_target_is_blocked():
    assert decide(status="AMBIGUOUS_SESSION", attempts=0)["action"] == "BLOCK"


def test_high_risk_timeout_requires_arbitration():
    assert decide(status="ACK_TIMEOUT", attempts=2, side_effect_risk="high")["action"] == "ARBITRATE"
