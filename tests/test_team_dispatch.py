import json
from pathlib import Path
import tempfile

import pytest

from agent_evolution_protocol.coordination import presence, team_dispatch


def _roots(folder):
    root = Path(folder) / "coordination"
    bridge = Path(folder) / "bridge"
    return root, bridge


def test_weak_online_agent_can_queue_mailbox_only_and_get_reply():
    with tempfile.TemporaryDirectory() as folder:
        root, bridge = _roots(folder)
        presence.signal(root, agent="weak_agent", state="online", source="agent-self-report",
                        session_id="old-thread")
        request = team_dispatch.create(root, sender="weak_agent", to="unadapted_agent",
                                       task="T130", instruction="Please review the error",
                                       evidence=["K:/evidence/error.json"], session_id="old-thread")
        receipt = team_dispatch.process_one(root, bridge,
                                            root / "delegation" / "inbox" / (request["id"] + ".json"))
        assert receipt["state"] == "MAILBOX_ONLY"
        assert receipt["agent_accepted"] is False
        assert (bridge / "messages" / "unadapted_agent" / (request["id"] + ".json")).is_file()
        assert team_dispatch.process(root, bridge)["processed"] == []
        presence.signal(root, agent="unadapted_agent", state="online", source="agent-self-report")
        answered = team_dispatch.acknowledge(root, bridge, event_id=request["id"],
                                             agent="unadapted_agent", session_id="",
                                             reply="I will inspect the compiler log.", accepted=True)
        assert answered["state"] == "AGENT_ACK"
        back = bridge / "messages" / "weak_agent" / (request["id"] + "-reply.json")
        assert json.loads(back.read_text(encoding="utf-8"))["reply_to"] == request["id"]


def test_offline_or_wrong_session_cannot_command():
    with tempfile.TemporaryDirectory() as folder:
        root, _ = _roots(folder)
        with pytest.raises(ValueError, match="SENDER_NOT_ONLINE"):
            team_dispatch.create(root, sender="weak_agent", to="qoder", task="T130",
                                 instruction="Analyze", evidence=["evidence"])
        presence.signal(root, agent="weak_agent", state="online", source="session_hook:start",
                        session_id="original")
        with pytest.raises(ValueError, match="SENDER_SESSION_MISMATCH"):
            team_dispatch.create(root, sender="weak_agent", to="qoder", task="T130",
                                 instruction="Analyze", evidence=["evidence"], session_id="wrong")


def test_generic_adapter_wake_is_queued_not_counted_as_ack():
    with tempfile.TemporaryDirectory() as folder:
        root, bridge = _roots(folder)
        presence.signal(root, agent="weak_agent", state="online", source="test")
        request = team_dispatch.create(root, sender="weak_agent", to="workbuddy", task="T130",
                                       instruction="Check a separate hypothesis", evidence=["error log"])
        receipt = team_dispatch.process_one(root, bridge,
                                            root / "delegation" / "inbox" / (request["id"] + ".json"))
        assert receipt["state"] == "WAKE_QUEUED_UNVERIFIED"
        wake = json.loads((bridge / "wake_queue" / (request["id"] + ".json")).read_text(encoding="utf-8"))
        assert wake["allow_new_session"] is False
        assert request["id"] in wake["deliver_text"]
        assert receipt["agent_accepted"] is False


def test_unadapted_target_gets_one_online_relay_not_false_ack():
    with tempfile.TemporaryDirectory() as folder:
        root, bridge = _roots(folder)
        presence.signal(root, agent="weak_agent", state="online", source="test")
        presence.signal(root, agent="workbuddy", state="online", source="session_hook:start")
        request = team_dispatch.create(root, sender="weak_agent", to="unknown_peer", task="T130",
                                       instruction="Please compare approaches", evidence=["a report"])
        receipt = team_dispatch.process_one(root, bridge,
                                            root / "delegation" / "inbox" / (request["id"] + ".json"))
        assert receipt["state"] == "RELAY_QUEUED_UNVERIFIED"
        assert receipt["relay"] == "workbuddy"
        assert receipt["agent_accepted"] is False
        assert (bridge / "wake_queue" / (request["id"] + "-relay.json")).is_file()


def test_file_only_request_is_validated_and_rejected_without_online_sender():
    with tempfile.TemporaryDirectory() as folder:
        root, bridge = _roots(folder)
        id_ = "D20261013T120000000000Z-123456abcdef"
        path = root / "delegation" / "inbox" / (id_ + ".json")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"id": id_, "from": "offline", "to": "qoder",
                                    "task": "T130", "instruction": "Do work", "evidence": ["x"],
                                    "priority": "normal", "allow_new_session": False,
                                    "reply_due_at": "2026-10-13T13:00:00+00:00"}), encoding="utf-8")
        receipt = team_dispatch.process_one(root, bridge, path)
        assert receipt["state"] == "REJECTED"
        assert not (bridge / "wake_queue" / (id_ + ".json")).exists()
