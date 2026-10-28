import json
import pytest
from agent_evolution_protocol.coordination.model_contract import validate_proposal, safe_fallback

def test_valid_json_proposal_is_normalized():
    result = validate_proposal(json.dumps({"intent": "read inbox", "operation": "read", "confidence": .8, "reason": "file evidence"}))
    assert result["confidence"] == .8

@pytest.mark.parametrize("value", ["not json", {"intent": "x", "operation": "send", "confidence": 2, "reason": "x"}, {"intent": "x", "operation": "read", "confidence": .5, "reason": "x", "shell": "rm -rf"}])
def test_invalid_proposals_are_rejected(value):
    with pytest.raises(ValueError): validate_proposal(value)

def test_safe_fallback_never_executes():
    assert safe_fallback(None)["execution_started"] is False
