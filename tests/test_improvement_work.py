from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from agent_evolution_protocol.coordination import improvement, improvement_work as work


class WorkTests(unittest.TestCase):
    def test_damaged_checkpoint_blocks_reclaim_without_replacing_lease(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            artifact = root / "artifact"
            artifact.write_text("frozen")
            identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            lease = work.claim(root, identity, actor="first", ttl=1)
            work.checkpoint(root, identity, actor="first", token=lease["token"],
                            evidence=[str(artifact)], note="handoff")
            previous = work._path(root, identity).read_bytes()
            artifact.write_text("mutated")
            with patch.object(work, "_now", return_value=lease["expires"] + 1):
                with self.assertRaisesRegex(ValueError, "checkpoint evidence changed"):
                    work.claim(root, identity, actor="next")
            self.assertEqual(work._path(root, identity).read_bytes(), previous)

    def test_brief_rechecks_lease_after_checkpoint_hashing(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            lease = work.claim(root, identity, actor="worker", ttl=10)
            original = work.checkpoints
            clock = [lease["claimed_at"]]
            def slow_check(*args):
                result = original(*args)
                clock[0] = lease["expires"]
                return result
            with patch.object(work, "_now", side_effect=lambda: clock[0]):
                with patch.object(work, "checkpoints", side_effect=slow_check):
                    with self.assertRaisesRegex(ValueError, "expired"):
                        work.brief(root, identity, actor="worker", token=lease["token"])

    def test_checkpoint_survives_reclaim_and_detects_changed_artifacts(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("baseline")
            output = root / "experiment.txt"
            output.write_text("partial result")
            identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            first = work.claim(root, identity, actor="first", ttl=1)
            saved = work.checkpoint(root, identity, actor="first", token=first["token"],
                                    evidence=[str(output)], note="Attempt A failed; try B")
            self.assertNotIn(first["token"], str(saved))
            self.assertFalse(saved["execution_stopped_verified"])
            with patch.object(work, "_now", return_value=first["expires"] + 1):
                with self.assertRaises(ValueError):
                    work.checkpoint(root, identity, actor="first", token=first["token"],
                                    evidence=[str(output)], note="stale write")
                second = work.claim(root, identity, actor="second")
                packet = work.brief(root, identity, actor="second", token=second["token"])
                self.assertEqual(packet["checkpoints"][0]["actor"], "first")
                work.checkpoint(root, identity, actor="second", token=second["token"],
                                evidence=[str(output)], note="B ready for review")
                self.assertEqual(len(work.checkpoints(root, identity)["checkpoints"]), 2)
                output.write_text("changed")
                with self.assertRaisesRegex(ValueError, "evidence changed"):
                    work.brief(root, identity, actor="second", token=second["token"])

    def test_checkpoint_integrity_and_stage_scope(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            lease = work.claim(root, identity, actor="tester")
            work.checkpoint(root, identity, actor="tester", token=lease["token"],
                            evidence=[str(proof)], note="saved")
            improvement.advance(root, identity, state="VALIDATED", actor="tester",
                                evidence=[str(proof)], note="passed")
            status = work.checkpoints(root, identity)
            self.assertEqual(status["checkpoints"], [])
            self.assertEqual(status["historical_count"], 1)
            path = root / "improvement_checkpoints" / identity / "000000.json"
            path.write_text(path.read_text(encoding="utf-8").replace('"saved"', '"forged"'), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "integrity"):
                work.checkpoints(root, identity)

    def test_brief_and_bounded_renewal(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            lease = work.claim(root, identity, actor="tester", ttl=30)
            packet = work.brief(root, identity, actor="tester", token=lease["token"])
            self.assertEqual(packet["action"], "validate")
            self.assertFalse(packet["authorization_verified"])
            extended = work.renew(root, identity, actor="tester", token=lease["token"], ttl=300)
            self.assertGreater(extended["expires"], lease["expires"])
            self.assertLessEqual(extended["expires"], lease["deadline"])
            with patch.object(work, "_now", return_value=extended["expires"] + 1):
                with self.assertRaises(ValueError):
                    work.renew(root, identity, actor="tester", token=lease["token"])
            proof.write_text("changed")
            with self.assertRaises(ValueError):
                work.brief(root, identity, actor="tester", token=lease["token"])
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
