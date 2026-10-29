from pathlib import Path
import json

from agent_evolution_protocol.coordination import handoff_worker, presence, team_dispatch


def test_evidence_backed_handoff_round_trip(tmp_path: Path):
    root, bridge = tmp_path / "coordination", tmp_path / "bridge"
    presence.signal(root, agent="commander", state="online", source="test", session_id="origin-1")
    request = team_dispatch.create(
        root, sender="commander", to="reference_agent", task="CampaignStatus",
        instruction="Summarize verified score and remaining quota.",
        evidence=["coordination/transcript_bus/snapshots/workbuddy.json"],
        session_id="origin-1",
    )
    deposited = team_dispatch.process_one(root, bridge, root / "delegation" / "inbox" / f"{request['id']}.json")
    assert deposited["state"] == "MAILBOX_ONLY"
    analysis = tmp_path / "analysis.md"
    analysis.write_text("Verified status: quota=26/30 used; T162 is seven-chip valid.", encoding="utf-8")
    result = handoff_worker.accept_and_reply(root, bridge, agent="reference_agent", event_id=request["id"],
                                              session_id="peer-original-7", analysis_file=analysis)
    assert result["state"] == "AGENT_ACK"
    artifact = json.loads(Path(result["artifact"]).read_text(encoding="utf-8"))
    assert artifact["event_id"] == request["id"]
    assert len(artifact["sha256"]) == 64
    reply = json.loads((bridge / "messages" / "commander" / f"{request['id']}-reply.json").read_text(encoding="utf-8"))
    assert request["id"] in reply["text"]
    assert "analysis_sha256=" in reply["text"]


def test_worker_rejects_wrong_target_without_ack(tmp_path: Path):
    root, bridge = tmp_path / "coordination", tmp_path / "bridge"
    presence.signal(root, agent="commander", state="online", source="test")
    request = team_dispatch.create(root, sender="commander", to="reference_agent", task="CampaignStatus",
                                   instruction="Analyze", evidence=["evidence"])
    team_dispatch.process_one(root, bridge, root / "delegation" / "inbox" / f"{request['id']}.json")
    analysis = tmp_path / "analysis.md"; analysis.write_text("x", encoding="utf-8")
    try:
        handoff_worker.accept_and_reply(root, bridge, agent="wrong_agent", event_id=request["id"],
                                        session_id="", analysis_file=analysis)
    except ValueError as exc:
        assert str(exc) == "HANDOFF_NOT_AVAILABLE"
    else:
        raise AssertionError("wrong target was accepted")


def test_inbox_lists_only_unfinished_directed_work(tmp_path: Path):
    root, bridge = tmp_path / "coordination", tmp_path / "bridge"
    presence.signal(root, agent="commander", state="online", source="test", session_id="origin-1")
    request = team_dispatch.create(root, sender="commander", to="reference_agent", task="CampaignStatus",
                                   instruction="Analyze", evidence=["evidence"], session_id="origin-1")
    team_dispatch.process_one(root, bridge, root / "delegation" / "inbox" / f"{request['id']}.json")
    rows = handoff_worker.inbox(root, bridge, agent="reference_agent")
    assert [row["event_id"] for row in rows] == [request["id"]]
    analysis = tmp_path / "analysis.md"; analysis.write_text("verified", encoding="utf-8")
    handoff_worker.accept_and_reply(root, bridge, agent="reference_agent", event_id=request["id"],
                                    session_id="peer-original-7", analysis_file=analysis)
    assert handoff_worker.inbox(root, bridge, agent="reference_agent") == []


def test_inbox_triage_places_urgent_and_exposes_deadline(tmp_path: Path):
    root, bridge = tmp_path / "coordination", tmp_path / "bridge"
    presence.signal(root, agent="commander", state="online", source="test", session_id="origin-1")
    normal = team_dispatch.create(
        root, sender="commander", to="reference_agent", task="NormalWork",
        instruction="Do the normal work.", evidence=["evidence"], session_id="origin-1",
        priority="normal", reply_minutes=60,
    )
    urgent = team_dispatch.create(
        root, sender="commander", to="reference_agent", task="UrgentWork",
        instruction="Read this first.", evidence=["evidence"], session_id="origin-1",
        priority="urgent", reply_minutes=30,
    )
    for request in (normal, urgent):
        team_dispatch.process_one(root, bridge, root / "delegation" / "inbox" / f"{request['id']}.json")
    rows = handoff_worker.inbox(root, bridge, agent="reference_agent")
    assert [row["event_id"] for row in rows] == [urgent["id"], normal["id"]]
    assert rows[0]["priority"] == "urgent"
    assert rows[0]["requires_immediate_read"] is True
    assert rows[0]["reply_due_at"] == urgent["reply_due_at"]
