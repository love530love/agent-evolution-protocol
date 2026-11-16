import pytest

from agent_evolution_protocol.coordination import plannotator_adapter as adapter
from agent_evolution_protocol.coordination.workspace_identity import new_workspace_id


def test_export_is_watermarked_idempotent_and_never_dispatches(tmp_path):
    workspace = new_workspace_id()
    first = adapter.export_review(tmp_path, workspace_id=workspace, event_id="PLAN-1",
                                  title="Review plan", plan_markdown="- verify receipt")
    second = adapter.export_review(tmp_path, workspace_id=workspace, event_id="PLAN-1",
                                   title="Review plan", plan_markdown="- verify receipt")
    text = open(first["markdown"], encoding="utf-8").read()
    assert first["status"] == "EXPORTED"
    assert second["status"] == "DUPLICATE"
    assert workspace in text and "PLAN-1" in text
    assert first["execution_started"] is False


def test_export_rejects_replacing_a_reviewed_event(tmp_path):
    workspace = new_workspace_id()
    adapter.export_review(tmp_path, workspace_id=workspace, event_id="PLAN-2", title="a", plan_markdown="one")
    with pytest.raises(ValueError, match="EXPORT_CONFLICT"):
        adapter.export_review(tmp_path, workspace_id=workspace, event_id="PLAN-2", title="a", plan_markdown="two")


def test_decision_requires_export_and_is_not_a_dispatch(tmp_path):
    workspace = new_workspace_id()
    with pytest.raises(ValueError, match="REVIEW_EXPORT_REQUIRED"):
        adapter.record_decision(tmp_path, workspace_id=workspace, event_id="PLAN-3", decision_id="D-1",
                                decision="approve", reviewer="reviewer", summary="okay")
    adapter.export_review(tmp_path, workspace_id=workspace, event_id="PLAN-3", title="a", plan_markdown="body")
    result = adapter.record_decision(tmp_path, workspace_id=workspace, event_id="PLAN-3", decision_id="D-1",
                                     decision="revise", reviewer="reviewer", summary="add evidence",
                                     annotations=[{"line": 4, "comment": "cite source"}])
    assert result["status"] == "RECORDED"
    assert result["decision"]["dispatch_performed"] is False
    assert result["decision"]["delivery_claim"] == "NONE"
