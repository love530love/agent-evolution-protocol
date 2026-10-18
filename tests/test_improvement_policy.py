from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from agent_evolution_protocol.coordination import improvement, improvement_policy as policy, improvement_work as work


class PolicyTests(unittest.TestCase):
    def test_enforced_mode_blocks_legacy_promotion_and_test_execution(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            self.assertEqual(policy.read(root)["mode"], "cooperative")
            policy.configure(root, mode="enforced", actor="operator", note="enable stage fencing")
            with self.assertRaises(ValueError):
                improvement.advance(root, identity, state="VALIDATED", actor="tester",
                                    evidence=[str(proof)], note="cannot bypass")
            with patch.object(improvement.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    improvement.validate_tests(root, identity, workspace=root, actor="tester")
                run.assert_not_called()
            lease = work.claim(root, identity, actor="tester")
            result = work.finish(root, identity, actor="tester", token=lease["token"],
                                 evidence=[str(proof)], note="verified")
            self.assertEqual(result["state"], "VALIDATED")
            # Rejection remains possible without a lease as a stop mechanism.
            improvement.advance(root, identity, state="REJECTED", actor="operator",
                                evidence=[str(proof)], note="stop")
            policy.configure(root, mode="cooperative", actor="operator", note="explicit compatibility rollback")
            self.assertEqual(len(policy.read(root)["history"]), 2)

    def test_corrupt_policy_fails_closed(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "improvement_policy.json").write_text('{"mode":"enforced","history":null}')
            with self.assertRaises(ValueError):
                policy.read(root)
            with self.assertRaises(ValueError):
                policy.configure(root, mode="cooperative", actor="a", note="cannot silently replace corruption")
