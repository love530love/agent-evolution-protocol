import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from agent_evolution_protocol.coordination import improvement_work
from agent_evolution_protocol.coordination import improvement as gate


class ExecutionTests(unittest.TestCase):
    def test_claim_expiry_during_real_test_blocks_promotion(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "tests").mkdir()
            (root / "tests" / "test_ok.py").write_text(
                "import unittest\nclass TestOK(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n")
            proof = root / "proof"
            proof.write_text("ok")
            identity = gate.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            lease = improvement_work.claim(root, identity, actor="tester")
            import subprocess
            original = subprocess.run
            def expired_after_run(*args, **kwargs):
                result = original(*args, **kwargs)
                path = improvement_work._path(root, identity)
                claim = json.loads(path.read_text())
                claim["expires"] = 0
                path.write_text(json.dumps(claim))
                return result
            with patch.object(gate.subprocess, "run", side_effect=expired_after_run):
                result = gate.validate_tests(root, identity, workspace=root, actor="tester", token=lease["token"])
            self.assertEqual(result["status"], "PROMOTION_BLOCKED")
            self.assertEqual(result["proposal"]["state"], "PROPOSED")
            self.assertEqual(json.loads(Path(result["report"]).read_text())["status"], "PASSED")
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
