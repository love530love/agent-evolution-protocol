import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_evolution_protocol import cli


def card(cell):
    return {"candidate_id": f"{cell}-v1", "cell_id": cell, "hypothesis": "testable", "structural_difference": "different", "prediction": "observable", "cheapest_falsification": "offline", "max_budget": 1, "stop_condition": "contradiction", "artifact_sha256": None, "contaminated": False}


class ProtocolTest(unittest.TestCase):
    def test_commit_reveal_hides_content_and_rejects_early_reveal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cli.init_round(SimpleNamespace(root=root, round_id="r1", task="demo", leader="lead", cells=["a", "b"], budget=4, reserve=0.2))
            a = root / "a.json"; b = root / "b.json"
            a.write_text(json.dumps(card("a")), encoding="utf-8")
            b.write_text(json.dumps(card("b")), encoding="utf-8")
            cli.commit(SimpleNamespace(root=root, round_id="r1", card=a))
            self.assertNotIn("testable", (root / "r1" / "events.jsonl").read_text(encoding="utf-8"))
            with self.assertRaises(cli.ProtocolError):
                cli.reveal(SimpleNamespace(root=root, round_id="r1", card=a))
            cli.commit(SimpleNamespace(root=root, round_id="r1", card=b))
            cli.reveal(SimpleNamespace(root=root, round_id="r1", card=a))
            self.assertTrue((root / "r1" / "revealed" / "a-v1.json").exists())
            self.assertEqual([], cli.audit_events(cli.get_events(root / "r1")))

    def test_hash_chain_detects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cli.init_round(SimpleNamespace(root=root, round_id="r1", task="demo", leader="lead", cells=["a", "b"], budget=4, reserve=0.2))
            ledger = root / "r1" / "events.jsonl"
            event = json.loads(ledger.read_text(encoding="utf-8"))
            event["actor"] = "tampered"
            ledger.write_text(json.dumps(event) + "\n", encoding="utf-8")
            self.assertTrue(cli.audit_events(cli.get_events(root / "r1")))

    def test_hold_blocks_commit_until_human_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cli.init_round(SimpleNamespace(root=root, round_id="r1", task="demo", leader="lead", cells=["a", "b"], budget=4, reserve=0.2))
            a = root / "a.json"; a.write_text(json.dumps(card("a")), encoding="utf-8")
            cli.hold(SimpleNamespace(root=root, round_id="r1", actor="guardian", reason="review", kind="HUMAN_HOLD"))
            with self.assertRaises(cli.ProtocolError):
                cli.commit(SimpleNamespace(root=root, round_id="r1", card=a))
            with self.assertRaises(cli.ProtocolError):
                cli.resume(SimpleNamespace(root=root, round_id="r1", actor="lead", reason="continue", human_approved=False))
            cli.resume(SimpleNamespace(root=root, round_id="r1", actor="human", reason="approved", human_approved=True))
            cli.commit(SimpleNamespace(root=root, round_id="r1", card=a))

    def test_governance_cards_are_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cli.init_round(SimpleNamespace(root=root, round_id="r1", task="demo", leader="lead", cells=["a", "b"], budget=4, reserve=0.2))
            challenge = {"challenged_assumption": "dominant route", "alternative_hypothesis": "alternative", "structural_difference": "new mechanism", "predicted_signature": "different output", "cheapest_falsification": "offline check", "max_budget": 1, "stop_condition": "contradiction"}
            path = root / "challenge.json"; path.write_text(json.dumps(challenge), encoding="utf-8")
            cli.governance_event(SimpleNamespace(root=root, round_id="r1", actor="catfish", file=path, command="challenge"))
            self.assertEqual("CHALLENGE_REQUEST", cli.get_events(root / "r1")[-1]["type"])


if __name__ == "__main__":
    unittest.main()
