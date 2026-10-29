from agent_evolution_protocol.coordination import uia_transport


def test_uia_requires_executor_before_input():
    result = uia_transport.deliver("qoder_cn", event_id="e1", text="x", execute=True)
    assert result["status"] in {"UIA_EXECUTOR_REQUIRED", "NO_VISIBLE_WINDOW", "AMBIGUOUS_WINDOW"}
