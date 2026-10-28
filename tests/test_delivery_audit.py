"""Do not promote file deposits or mailbox reads into Agent acceptance."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from agent_evolution_protocol.coordination.delivery_audit import audit
from agent_evolution_protocol import cli
from contextlib import redirect_stdout
from io import StringIO


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
            self.assertEqual(result["next_required"], {"AGENT_ACK": 1, "ARTIFACT_VERIFIED": 1, "CLIENT_BOUND": 1})
            self.assertEqual(result["rows"][0]["next_required"], "CLIENT_BOUND")
            self.assertFalse(result["rows"][0]["agent_accepted"])

    def test_ignores_malformed_and_mismatched_paths(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            put(root / "messages" / "qoder" / "x.json", {"id": "different", "to": "qoder"})
            put(root / "messages" / "qoder" / "y.json", {"id": "y", "to": "workbuddy"})
            (root / "messages" / "qoder" / "bad.json").write_text("{bad", encoding="utf-8")
            self.assertEqual(audit(root)["messages"], 0)

    def test_cli_exposes_read_only_next_evidence_summary(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            put(root / "messages" / "qoder" / "m.json", {"id": "m", "to": "qoder", "topic": "T1"})
            args = cli.build_parser().parse_args(["--coord-root", str(root), "coord-delivery-audit", "--summary-only"])
            output = StringIO()
            with redirect_stdout(output):
                args.func(args)
            result = json.loads(output.getvalue())
            self.assertEqual(result["next_required"], {"CLIENT_BOUND": 1})
            self.assertNotIn("rows", result)


if __name__ == "__main__":
    unittest.main()
