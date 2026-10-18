from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from agent_evolution_protocol.coordination import improvement, improvement_work as work


class WorkTests(unittest.TestCase):
    def test_claim_expiry_fencing_review_and_release(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("verified")
            identity = improvement.propose(root, dict(author="author", change="fix", acceptance="pass",
                rollback="restore", scope="fixture", evidence=[str(proof)]))["id"]
            first = work.claim(root, identity, actor="tester", ttl=1)
            self.assertFalse(work.pending(root)["work"][0]["available"])
            with self.assertRaises(ValueError):
                work.claim(root, identity, actor="other")
            with patch.object(work, "_now", return_value=first["expires"] + 1):
                second = work.claim(root, identity, actor="replacement")
                with self.assertRaises(ValueError):
                    work.finish(root, identity, actor="tester", token=first["token"], release=True)
                work.finish(root, identity, actor="replacement", token=second["token"],
                            evidence=[str(proof)], note="manually verified")
            with self.assertRaises(ValueError):
                work.claim(root, identity, actor="author")
            review = work.claim(root, identity, actor="reviewer")
            work.finish(root, identity, actor="reviewer", token=review["token"], release=True)
            self.assertEqual(improvement.status(root, identity)["state"], "VALIDATED")
            self.assertTrue(work.pending(root)["work"][0]["available"])

    def test_external_state_change_invalidates_claim(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            lease = work.claim(root, identity, actor="v")
            improvement.advance(root, identity, state="VALIDATED", actor="v", evidence=[str(proof)], note="pass")
            with self.assertRaises(ValueError):
                work.finish(root, identity, actor="v", token=lease["token"], release=True)
            self.assertEqual(work.pending(root)["work"][0]["action"], "review")
