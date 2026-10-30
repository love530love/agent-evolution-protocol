from pathlib import Path

import pytest

from agent_evolution_protocol.coordination.execution_plane import (
    DeliveryEvidence,
    classify_observation,
    route_candidates,
)
from agent_evolution_protocol.coordination.execution_provider_catalog import load


ROOT = Path(__file__).parents[1]


def test_qoder_like_input_only_observation_is_not_success():
    assert classify_observation(
        session_bound=True, input_submitted=True, message_visible=False, agent_ack=False
    ) == "MESSAGE_SEND_UNCONFIRMED"


def test_ack_requires_the_full_evidence_chain():
    with pytest.raises(ValueError, match="preceding evidence"):
        DeliveryEvidence(
            provider="qoder",
            workspace_id="ws-1",
            session_id="session-1",
            event_id="evt-1",
            operation="send",
            agent_ack=True,
        ).validate()


def test_candidate_provider_cannot_enter_production_dispatch():
    catalog = load(ROOT / "examples" / "execution-provider-catalog.json")
    plan = route_candidates(catalog, layer="desktop_visual", operation="dispatch")
    assert plan["status"] == "NO_PRODUCTION_PROVIDER"
    assert plan["candidates"] == []


def test_task_tested_provider_can_be_planned_only_after_ack_evidence():
    catalog = load(ROOT / "examples" / "execution-provider-catalog.json")
    catalog["providers"][0]["status"] = "task-tested"
    evidence = DeliveryEvidence(
        provider=catalog["providers"][0]["id"],
        workspace_id="ws-1",
        session_id="tab-1",
        event_id="evt-1",
        operation="dispatch",
        input_submitted=True,
        message_visible=True,
        agent_ack=True,
    )
    # The first catalog row is a browser provider, so it is excluded by layer.
    catalog["providers"].append({
        "id": "desktop_test",
        "repository": "https://github.com/example/desktop-test",
        "layer": "desktop_visual",
        "integration": "connector",
        "runtime": ["desktop"],
        "capabilities": ["dispatch"],
        "strengths": ["test"],
        "limitations": ["test"],
        "acceptance": ["test"],
        "status": "task-tested",
    })
    evidence = DeliveryEvidence(
        provider="desktop_test", workspace_id="ws-1", session_id="window-1",
        event_id="evt-1", operation="dispatch", input_submitted=True,
        message_visible=True, agent_ack=True,
    )
    plan = route_candidates(catalog, layer="desktop_visual", operation="dispatch", evidence=[evidence])
    assert plan["status"] == "READY_TO_PLAN"
    assert plan["candidates"][0]["provider"] == "desktop_test"
