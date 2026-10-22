from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from agent_evolution_protocol.coordination import improvement, improvement_dispatch as router, commander, improvement_work


class DispatchTests(unittest.TestCase):
    def test_owner_expiry_release_and_historical_ack_are_distinct(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            head = improvement.status(root, identity)["events"][-1]["sha256"]
            order = commander.submit_order(root, target="worker", task="improvement:" + identity,
                text="verify", sender="router", event_id="EVT-IMPROVE-" + head[:24].upper())
            commander._record_attempt(root, order, state="AGENT_ACK", note="fixture, not live ACK")
            commander._record_attempt(root, order, state="FAILED")
            row = router.delivery_status(root)["orders"][0]
            self.assertTrue(row["ack_recorded"])
            self.assertEqual(row["progress"], "DELIVERY_BLOCKED")
            lease = improvement_work.claim(root, identity, actor="other")
            row = router.delivery_status(root)["orders"][0]
            self.assertEqual(row["progress"], "CLAIMED_ELSEWHERE")
            self.assertEqual(row["claim_owner"], "other")
            self.assertFalse(row["target_claimed"])
            with patch.object(improvement_work, "_now", return_value=lease["expires"]):
                self.assertEqual(router.delivery_status(root)["orders"][0]["progress"], "CLAIM_EXPIRED")
            improvement_work.finish(root, identity, actor="other", token=lease["token"], release=True)
            result = router.delivery_status(root)
            self.assertEqual(result["orders"][0]["progress"], "CLAIM_RELEASED")
            self.assertFalse(result["delivery_attempted"])
            self.assertFalse(result["ui_read_performed"])

    def test_obsolete_preinput_retirement_frees_route_and_fences_stale_copy(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            head = improvement.status(root, identity)["events"][-1]["sha256"]
            order = commander.submit_order(root, target="worker", task="improvement:" + identity,
                text="verify", sender="router", event_id="EVT-IMPROVE-" + head[:24].upper())
            with self.assertRaises(ValueError):
                router.retire(root, order_id=order["id"], actor="operator", note="not old yet")
            improvement.advance(root, identity, state="VALIDATED", actor="tester",
                                evidence=[str(proof)], note="tested")
            self.assertEqual(router.retire(root, order_id=order["id"], actor="operator", note="old stage")["state"], "RETIRED")
            with patch.object(commander.registry, "profile") as profile:
                self.assertEqual(commander.dispatch(root, order=order)["state"], "RETIRED")
                profile.assert_not_called()
            self.assertIsNone(commander.reinforce(root, order["id"]))
            self.assertEqual(router.delivery_status(root)["orders"][0]["progress"], "RETIRED")
            roster = {"agents": [{"agent": "worker", "actions": ["review"]}]}
            with patch.object(router.presence, "snapshot", return_value={"agents": [
                {"agent": "worker", "state": "online", "active_assignments": []}]}):
                self.assertEqual(router.schedule(root, roster, apply=True)["orders_queued"], 1)

    def test_uncertain_delivery_cannot_retire(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            order = commander.submit_order(root, target="worker", task="improvement:" + "a" * 24,
                                            text="verify", sender="router")
            commander._record_attempt(root, order, state="WINDOW_FOUND")
            with self.assertRaises(ValueError):
                router.retire(root, order_id=order["id"], actor="operator", note="do not retry")

    def test_ack_claim_and_stale_dispatch_are_distinct(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            head = improvement.status(root, identity)["events"][-1]["sha256"]
            order = commander.submit_order(root, target="worker", task="improvement:" + identity,
                text="verify", sender="router", event_id="EVT-IMPROVE-" + head[:24].upper())
            self.assertEqual(router.delivery_status(root)["orders"][0]["progress"], "AWAITING_ACK")
            commander._record_attempt(root, order, state="AGENT_ACK", note="fixture only")
            self.assertEqual(router.delivery_status(root)["orders"][0]["progress"], "ACK_WITHOUT_CLAIM")
            improvement_work.claim(root, identity, actor="worker")
            self.assertEqual(router.delivery_status(root)["orders"][0]["progress"], "CLAIMED")
            improvement.advance(root, identity, state="VALIDATED", actor="worker",
                                evidence=[str(proof)], note="tested")
            self.assertEqual(router.delivery_status(root)["orders"][0]["progress"], "STAGE_CHANGED")
            with patch.object(commander.registry, "profile") as profile:
                result = commander.dispatch(root, order=order)
                profile.assert_not_called()
            self.assertEqual(result["state"], "CEASED")
            self.assertIn("STALE_IMPROVEMENT_STAGE", result["attempts"][-1]["note"])

    def test_missing_proposal_blocks_before_desktop_discovery(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            order = commander.submit_order(root, target="worker", task="improvement:" + "a" * 24,
                                            text="verify", sender="router")
            with patch.object(commander.registry, "profile") as profile:
                self.assertEqual(commander.dispatch(root, order=order)["state"], "CEASED")
                profile.assert_not_called()

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
