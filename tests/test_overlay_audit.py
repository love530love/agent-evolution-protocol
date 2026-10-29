from agent_evolution_protocol.coordination.overlay_audit import read, record
from agent_evolution_protocol.coordination.overlay_detector import classify


def test_overlay_audit_is_append_only_and_queryable(tmp_path):
    overlay = classify(control_type="alert", title="执行连接失败")
    row = record(tmp_path, overlay, agent="qoder_cn", action="retry", verified=True,
                 event_id="e-1")
    assert row["verified"] is True
    rows = read(tmp_path, agent="qoder_cn")
    assert rows[0]["event_id"] == "e-1"
    assert rows[0]["overlay"]["kind"] == "alert"
