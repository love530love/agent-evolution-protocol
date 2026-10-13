"""Do not promote file deposits or mailbox reads into Agent acceptance."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from agent_evolution_protocol.coordination.delivery_audit import audit


def put(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class DeliveryAuditTests(unittest.TestCase):
    def test_separates_deposit_read_visible_and_agent_ack(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            for message_id in ("m1", "m2", "m3"):
                put(root / "messages" / "qoder" / f"{message_id}.json",
                    {"id": message_id, "to": "qoder", "topic": "T1", "kind": "warning"})
            put(root / "acks" / "qoder" / "m1.json", {"id": "m1", "agent": "qoder"})
            put(root / "commander" / "state" / "o2.json",
                {"id": "o2", "target": "qoder", "event_id": "m2", "state": "MESSAGE_VISIBLE"})
            put(root / "commander" / "state" / "o3.json",
                {"id": "o3", "target": "qoder", "event_id": "m3", "state": "AGENT_ACK",
                 "ack": {"evidence": "challenge-reply"}})
            result = audit(root, topic="T1")
            self.assertEqual(result["messages"], 3)
            self.assertEqual(result["mailbox_read"], 1)
            self.assertEqual(result["message_visible"], 2)
            self.assertEqual(result["agent_accepted"], 1)
            self.assertFalse(result["rows"][0]["agent_accepted"])

    def test_ignores_malformed_and_mismatched_paths(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            put(root / "messages" / "qoder" / "x.json", {"id": "different", "to": "qoder"})
            put(root / "messages" / "qoder" / "y.json", {"id": "y", "to": "workbuddy"})
            (root / "messages" / "qoder" / "bad.json").write_text("{bad", encoding="utf-8")
            self.assertEqual(audit(root)["messages"], 0)


if __name__ == "__main__":
    unittest.main()
