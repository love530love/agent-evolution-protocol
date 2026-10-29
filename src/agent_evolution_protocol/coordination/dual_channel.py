"""Combine protocol and GUI evidence without treating either as omniscient."""
from __future__ import annotations

from enum import Enum
from typing import Any


class WakeState(str, Enum):
    PROTOCOL_SENT = "PROTOCOL_SENT"
    PROTOCOL_ACK = "PROTOCOL_ACK"
    GUI_SESSION_ACTIVE = "GUI_SESSION_ACTIVE"
    GUI_MESSAGE_VISIBLE = "GUI_MESSAGE_VISIBLE"
    GUI_EXECUTING = "GUI_EXECUTING"
    RESULT_VERIFIED = "RESULT_VERIFIED"
    WAKE_UNPROVEN = "WAKE_UNPROVEN"
    DELIVERED_ACK_PENDING = "DELIVERED_ACK_PENDING"


def evaluate(*, protocol_sent: bool = False, protocol_ack: bool = False,
             gui_session: bool = False, gui_visible: bool = False,
             gui_executing: bool = False, result_verified: bool = False) -> dict[str, Any]:
    if result_verified:
        state = WakeState.RESULT_VERIFIED
    elif gui_executing:
        state = WakeState.GUI_EXECUTING
    elif gui_visible and protocol_ack:
        state = WakeState.GUI_MESSAGE_VISIBLE
    elif gui_visible:
        state = WakeState.DELIVERED_ACK_PENDING
    elif gui_session and protocol_sent:
        state = WakeState.GUI_SESSION_ACTIVE
    elif protocol_ack or protocol_sent:
        state = WakeState.WAKE_UNPROVEN
    else:
        state = WakeState.WAKE_UNPROVEN
    return {
        "state": state.value,
        "protocol_sent": protocol_sent,
        "protocol_ack": protocol_ack,
        "gui_session": gui_session,
        "gui_visible": gui_visible,
        "gui_executing": gui_executing,
        "result_verified": result_verified,
        "requires_gui_check": state in (WakeState.WAKE_UNPROVEN, WakeState.PROTOCOL_ACK),
    }
