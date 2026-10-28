from agent_evolution_protocol.coordination.capability_metrics import record, rank

def test_metrics_rank_successful_provider_first():
    metrics = {}
    record(metrics, "uia", success=True, latency_ms=100, evidence_quality=1)
    record(metrics, "cdp", success=False, latency_ms=1000, evidence_quality=0, failure_signature="no_ack")
    assert rank(metrics)[0]["provider"] == "uia"
    assert rank(metrics)[1]["top_failure"] == "no_ack"
