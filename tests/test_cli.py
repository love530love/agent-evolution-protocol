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


if __name__ == "__main__":
    unittest.main()
