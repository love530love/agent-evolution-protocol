from datetime import datetime, timezone, timedelta

import pytest

from agent_evolution_protocol.coordination import dispatch_contract


def _envelope(**overrides):
    now = datetime.now(timezone.utc)
    payload = dispatch_contract.build(
        order_id="ORD-12345678",
        event_id="EVT-12345678",
        workspace_id="W-12345678",
        sender="codex",
        target="qoder",
        task="T125",
        instruction="Inspect the workspace. EVT-12345678",
        priority="normal",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
    )
    payload.update(overrides)
    return payload


def test_build_contains_reusable_evidence_and_retry_contract():
    value = _envelope()
    assert value["schema"] == "aep-dispatch-envelope-v1"
    assert value["session_policy"]["mode"] == "sticky"
    assert value["retry_policy"] == {
        "max_attempts": 1, "retry_only_preinput": True, "same_event_id": True
    }
    assert "agent_ack" in value["evidence_required"]
    assert dispatch_contract.validate(value, strict=True)["event_id"] == "EVT-12345678"


def test_sticky_dispatch_cannot_allow_new_session():
    value = _envelope()
    value["session_policy"]["allow_new_session"] = True
    with pytest.raises(ValueError, match="cannot allow"):
        dispatch_contract.validate(value)


def test_new_session_requires_handoff_packet():
    value = _envelope()
    value["session_policy"] = {
        "mode": "new-session", "allow_new_session": True, "same_workspace": True
    }
    with pytest.raises(ValueError, match="handoff_packet_ref"):
        dispatch_contract.validate(value)


def test_event_id_must_be_in_instruction_and_retries_are_fenced():
    value = _envelope(instruction="no event id")
    with pytest.raises(ValueError, match="carry event_id"):
        dispatch_contract.validate(value)
    value = _envelope()
    value["retry_policy"]["retry_only_preinput"] = False
    with pytest.raises(ValueError, match="pre-input-only"):
        dispatch_contract.validate(value)


def test_strict_mode_rejects_legacy_compatibility_envelope():
    value = dispatch_contract.build(
        order_id="ORD-12345678", event_id="EVT-12345678", workspace_id="",
        sender="codex", target="qoder", task="T125",
        instruction="Inspect. EVT-12345678", priority="normal",
        created_at="2026-10-30T00:00:00+00:00",
        expires_at="2026-10-30T01:00:00+00:00",
    )
    assert value["legacy_compat"] is True
    with pytest.raises(ValueError, match="legacy_compat"):
        dispatch_contract.validate(value, strict=True)
