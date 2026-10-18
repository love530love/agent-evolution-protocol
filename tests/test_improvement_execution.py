import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from agent_evolution_protocol.coordination import improvement as gate


class ExecutionTests(unittest.TestCase):
    def test_event_to_actual_tests_and_no_automatic_review(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_example.py").write_text(
                "import unittest\nclass TestExample(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n")
            event = root / "event.json"
            payload = {"event_id": "completed-1", "kind": "TASK_COMPLETED", "improvement": {
                "author": "writer", "change": "example", "acceptance": "test passes",
                "scope": "fixture", "rollback": "discard fixture", "evidence": []}}
            event.write_text(json.dumps(payload))
            proposal = gate.ingest(root, event)
            self.assertEqual(gate.ingest(root, event)["id"], proposal["id"])
            result = gate.validate_tests(root, proposal["id"], workspace=root, actor="tester")
            self.assertEqual(result["tests_run"], 1)
            self.assertEqual(result["proposal"]["state"], "VALIDATED")
            self.assertFalse(result["security_sandbox"])
            payload["kind"] = "TASK_FAILED"
            event.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                gate.ingest(root, event)

    def test_empty_suite_cannot_promote(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "tests").mkdir()
            evidence = root / "proof"
            evidence.write_text("baseline")
            proposal = gate.propose(root, dict(author="a", change="b", acceptance="c",
                        scope="d", rollback="e", evidence=[str(evidence)]))
            result = gate.validate_tests(root, proposal["id"], workspace=root, actor="tester")
            self.assertEqual(result["status"], "FAILED")
            self.assertEqual(result["proposal"]["state"], "PROPOSED")
