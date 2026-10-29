from agent_evolution_protocol.coordination.dual_channel import evaluate


def test_protocol_success_without_gui_is_unproven():
    assert evaluate(protocol_sent=True, protocol_ack=True)["state"] == "WAKE_UNPROVEN"


def test_gui_delivery_without_ack_waits_for_ack():
    assert evaluate(gui_visible=True)["state"] == "DELIVERED_ACK_PENDING"


def test_both_channels_prove_visibility():
    assert evaluate(protocol_ack=True, gui_visible=True)["state"] == "GUI_MESSAGE_VISIBLE"


def test_result_is_strongest_state():
    assert evaluate(protocol_ack=True, gui_executing=True, result_verified=True)["state"] == "RESULT_VERIFIED"
