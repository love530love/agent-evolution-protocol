from pathlib import Path
from agent_evolution_protocol.coordination.ack_tracker import open_request, acknowledge, wait_for_ack


def test_ack_round_trip(tmp_path: Path):
    open_request(tmp_path, event_id="e1", agent="codex")
    acknowledge(tmp_path, event_id="e1", agent="codex", detail="accepted")
    assert wait_for_ack(tmp_path, event_id="e1")["state"] == "acknowledged"


def test_ack_timeout_is_bounded(tmp_path: Path):
    open_request(tmp_path, event_id="e2", agent="qoder")
    assert wait_for_ack(tmp_path, event_id="e2", timeout_seconds=0)["state"] == "ack_timeout"
