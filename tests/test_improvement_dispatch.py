from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from agent_evolution_protocol.coordination import improvement, improvement_dispatch as router, commander


class DispatchTests(unittest.TestCase):
    def test_preview_queue_dedup_and_review_independence(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("verified")
            identity = improvement.propose(root, dict(author="author", change="fix", acceptance="pass",
                rollback="restore", scope="fixture", evidence=[str(proof)]))["id"]
            roster = {"agents": [{"agent": "worker", "actions": ["validate"]},
                                 {"agent": "reviewer", "actions": ["review"]}]}
            online = {"agents": [{"agent": name, "state": "online", "active_assignments": []}
                                 for name in ("worker", "reviewer")]}
            with patch.object(router.presence, "snapshot", return_value=online):
                self.assertEqual(router.schedule(root, roster)["orders_queued"], 0)
                self.assertFalse((root / "commander").exists())
                self.assertEqual(router.schedule(root, roster, apply=True)["orders_queued"], 1)
                self.assertEqual(router.schedule(root, roster, apply=True)["orders_queued"], 0)
                self.assertEqual(len(commander._orders(root)), 1)
                self.assertFalse(commander._orders(root)[0]["allow_launch"])
                improvement.advance(root, identity, state="VALIDATED", actor="worker",
                                    evidence=[str(proof)], note="tested")
                result = router.schedule(root, roster, apply=True)
                self.assertEqual(result["assignments"][0]["target"], "reviewer")
                self.assertEqual(result["orders_queued"], 1)

    def test_offline_and_invalid_roster_do_not_queue(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                router.schedule(root, {"agents": [{"agent": "bad/name", "actions": ["review"]}]})
            with patch.object(router.presence, "snapshot", return_value={"agents": []}):
                self.assertEqual(router.schedule(root, {"agents": []}, apply=True)["orders_queued"], 0)
