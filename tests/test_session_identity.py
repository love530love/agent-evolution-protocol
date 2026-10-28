from agent_evolution_protocol.coordination.session_identity import parse, validate

def test_parse_unique_title():
    assert parse("Qoder-CN-FlagGems-Compiler")["role"] == "Compiler"

def test_reject_ambiguous_title():
    assert validate("Qoder", agent="Qoder", workspace="FlagGems")["ok"] is False

def test_validate_workspace_and_region():
    assert validate("WorkBuddy-CN-AEP-Executor", agent="WorkBuddy", workspace="AEP", region="CN")["ok"] is True
