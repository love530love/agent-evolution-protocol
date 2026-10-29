from agent_evolution_protocol.coordination.clawchat_transport import ClawChatConfig, envelope, send


def test_clawchat_defaults_to_preview():
    msg = envelope(event_id="e1", sender="aep", target="qoder_cn", text="hello")
    result = send(ClawChatConfig(), msg)
    assert result["status"] == "PREVIEW"
    assert result["message"]["event_id"] == "e1"


def test_clawchat_requires_port_before_connecting():
    msg = envelope(event_id="e2", sender="aep", target="workbuddy", text="hello")
    assert send(ClawChatConfig(), msg, execute=True)["status"] == "CONFIG_REQUIRED"
