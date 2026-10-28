from agent_evolution_protocol.coordination.task_envelope import validate, handoff

def test_envelope_preserves_evidence_and_dissent():
    row = validate({"task_id": "T1", "goal": "inspect", "requester": "codex", "phase": "debate", "next_action": "review", "dissent": ["risk"]})
    assert row["dissent"] == ["risk"]
    assert handoff(row, assignee="qoder", next_action="execute")["assignees"] == ["qoder"]
