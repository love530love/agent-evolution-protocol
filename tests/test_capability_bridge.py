import json

from agent_evolution_protocol.coordination.capability_bridge import plan, plan_proposal


def test_plan_prefers_task_tested_and_never_executes(tmp_path):
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps({"capabilities": [
        {"provider": "uia", "operations": ["read"], "status": "declared", "priority": 1},
        {"provider": "rules", "operations": ["read"], "status": "task-tested", "priority": 99},
    ]}), encoding="utf-8")
    result = plan("read the current conversation", "read", manifest=path)
    assert result["status"] == "PLAN_ONLY"
    assert result["execution_started"] is False
    assert result["candidates"][0]["provider"] == "rules"


def test_high_risk_requires_lease(tmp_path):
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps({"capabilities": [{"provider": "uia", "operations": ["send"]}]}), encoding="utf-8")
    result = plan("send a message", "send", manifest=path)
    assert result["status"] == "REQUIRES_EXPLICIT_LEASE"
    assert result["execution_started"] is False

def test_delegated_open_mode_allows_agent_owned_audit(tmp_path):
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps({"capabilities": [{"provider": "uia", "operations": ["send"]}]}), encoding="utf-8")
    result = plan("send a message", "send", manifest=path, execution_mode="delegated-open")
    assert result["status"] == "PLAN_ONLY"
    assert result["delegated_authority"] is True
    assert result["execution_started"] is False


def test_model_proposal_enters_same_non_executing_planner(tmp_path):
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps({"capabilities": [{"provider": "rules", "operations": ["read"], "status": "task-tested"}]}), encoding="utf-8")
    result = plan_proposal({"intent": "read inbox", "operation": "read", "confidence": .9, "reason": "need evidence"}, manifest=path)
    assert result["status"] == "PLAN_ONLY"
    assert result["execution_started"] is False
    assert result["model_confidence"] == .9
