import json

from agent_evolution_protocol.coordination.capability_bridge import plan


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
