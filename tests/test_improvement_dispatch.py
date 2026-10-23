from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from agent_evolution_protocol.coordination import improvement, improvement_dispatch as router, commander, improvement_work


class DispatchTests(unittest.TestCase):
    def test_dispatch_defers_claimed_work_and_stops_on_damaged_lease(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            head = improvement.status(root, identity)["events"][-1]["sha256"]
            order = commander.submit_order(root, target="worker", task="improvement:" + identity,
                text="verify", sender="router", event_id="EVT-IMPROVE-" + head[:24].upper())
            lease = improvement_work.claim(root, identity, actor="owner")
            with patch.object(commander.registry, "profile") as profile:
                result = commander.dispatch(root, order=order)
                self.assertEqual(result["state"], "DEFERRED")
                self.assertIn("ALREADY_CLAIMED", result["attempts"][-1]["note"])
                profile.assert_not_called()
            improvement_work.finish(root, identity, actor="owner", token=lease["token"], release=True)
            with patch.object(commander.registry, "profile", side_effect=ValueError("not registered")) as profile:
                commander.dispatch(root, order=order)
                profile.assert_called_once()
            improvement_work._path(root, identity).write_text("{}", encoding="utf-8")
            with patch.object(commander.registry, "profile") as profile:
                result = commander.dispatch(root, order=order)
                self.assertEqual(result["state"], "CEASED")
                self.assertIn("INVALID_IMPROVEMENT_LEASE", result["attempts"][-1]["note"])
                profile.assert_not_called()

    def test_unavailable_members_have_specific_non_authorizing_reasons(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identity = improvement.propose(root, dict(author="author", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            improvement.advance(root, identity, state="VALIDATED", actor="tester",
                                evidence=[str(proof)], note="fixture")
            roster = {"agents": [{"agent": name, "actions": actions} for name, actions in (
                ("author", ["review"]), ("occupied", ["review"]), ("absent", ["review"]),
                ("coder", ["validate"]))]}
            observed = {"agents": [{"agent": name, "state": "online", "active_assignments": tasks}
                for name, tasks in (("author", []), ("occupied", ["other-task"]), ("coder", []))]}
            with patch.object(router.presence, "snapshot", return_value=observed):
                result = router.schedule(root, roster, apply=True)
            row = result["assignments"][0]
            reasons = {r["agent"]: r["reasons"] for r in row["candidates"]}
            self.assertEqual(reasons["author"], ["REVIEW_NOT_INDEPENDENT"])
            self.assertEqual(reasons["occupied"], ["KNOWN_ASSIGNMENT_OR_ORDER"])
            self.assertEqual(reasons["absent"], ["NO_FRESH_ONLINE_DECLARATION"])
            self.assertEqual(reasons["coder"], ["CAPABILITY_NOT_DECLARED"])
            self.assertEqual(row["status"], "NO_ELIGIBLE_MEMBER")
            self.assertEqual(commander._orders(root), [])
            self.assertFalse(result["delivery_attempted"])

    def test_stage_changes_before_and_after_selection_do_not_queue_old_action(self):
        for change_on in (1, 2):
            with self.subTest(change_on=change_on), TemporaryDirectory() as temp:
                root = Path(temp)
                proof = root / "proof"
                proof.write_text("ok")
                identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                    rollback="d", scope="e", evidence=[str(proof)]))["id"]
                pending = improvement_work.pending(root)
                original = improvement.status
                calls = [0]
                def status_with_change(*args):
                    calls[0] += 1
                    if calls[0] == change_on:
                        # advance reads status too; keep the injected race single-shot.
                        with patch.object(improvement, "status", original):
                            improvement.advance(root, identity, state="VALIDATED", actor="worker",
                                                evidence=[str(proof)], note="concurrent fixture")
                    return original(*args)
                with patch.object(router.presence, "snapshot", return_value={"agents": [
                        {"agent": "worker", "state": "online", "active_assignments": []}]}):
                    with patch.object(improvement_work, "pending", return_value=pending):
                        with patch.object(improvement, "status", side_effect=status_with_change):
                            result = router.schedule(root, {"agents": [{"agent": "worker", "actions": ["validate"]}]}, apply=True)
                self.assertEqual(result["orders_queued"], 0)
                self.assertEqual(result["assignments"][0]["status"], "STAGE_CHANGED")
                self.assertEqual(commander._orders(root), [])

    def test_one_unreadable_proposal_does_not_stop_healthy_work(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            good = improvement_work.pending(root)["work"][0]
            bad = {**good, "id": "a" * 24}
            original = improvement.status
            def reader(root, key):
                if key == bad["id"]:
                    raise ValueError("fixture damaged")
                return original(root, key)
            with patch.object(router.presence, "snapshot", return_value={"agents": [
                    {"agent": "worker", "state": "online", "active_assignments": []}]}):
                with patch.object(improvement_work, "pending", return_value={"work": [bad, good]}):
                    with patch.object(improvement, "status", side_effect=reader):
                        result = router.schedule(root, {"agents": [{"agent": "worker", "actions": ["validate"]}]}, apply=True)
            self.assertEqual(result["blocked_count"], 1)
            self.assertEqual(result["orders_queued"], 1)
            self.assertEqual(result["assignments"][0]["status"], "WORK_DAMAGED")

    def test_direct_dispatch_does_not_replay_stale_or_uncertain_orders(self):
        from copy import deepcopy
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            head = improvement.status(root, identity)["events"][-1]["sha256"]
            for state in ("WINDOW_FOUND", "INPUT_SUBMITTED", "MESSAGE_VISIBLE", "AGENT_ACK", "FAILED", "CEASED"):
                with self.subTest(state=state):
                    order = commander.submit_order(root, target="worker", task="improvement:" + identity,
                        text="verify", sender="router", event_id="EVT-IMPROVE-" + head[:24].upper())
                    stale = deepcopy(order)
                    commander._record_attempt(root, order, state=state)
                    with patch.object(commander.registry, "profile") as profile:
                        self.assertEqual(commander.dispatch(root, order=stale)["state"], state)
                        self.assertEqual(commander.dispatch(root, order=order)["state"], state)
                        profile.assert_not_called()
                    commander._record_attempt(root, order, state="DEFERRED")
                    with patch.object(commander.registry, "profile") as profile:
                        self.assertEqual(commander.dispatch(root, order=order)["state"], "DEFERRED")
                        profile.assert_not_called()
            fresh = commander.submit_order(root, target="worker", task="improvement:" + identity,
                text="verify", sender="router", event_id="EVT-IMPROVE-" + head[:24].upper())
            with patch.object(commander.registry, "profile", side_effect=ValueError("fixture unregistered")) as profile:
                self.assertEqual(commander.dispatch(root, order=fresh)["state"], "DEFERRED")
                self.assertEqual(commander.dispatch(root, order=fresh)["state"], "DEFERRED")
                self.assertEqual(profile.call_count, 2)  # Proven pre-input retries still work.
            (commander._root(root) / "inbox" / f"{fresh['id']}.json").write_text("{}", encoding="utf-8")
            with patch.object(commander.registry, "profile") as profile:
                with self.assertRaisesRegex(ValueError, "missing or unreadable"):
                    commander.dispatch(root, order=fresh)
                profile.assert_not_called()

    def test_reroute_preserves_order_and_fences_stale_dispatch(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            head = improvement.status(root, identity)["events"][-1]["sha256"]
            order = commander.submit_order(root, target="old", task="improvement:" + identity,
                text="verify", sender="router", event_id="EVT-IMPROVE-" + head[:24].upper())
            roster = {"agents": [{"agent": "new", "actions": ["validate"]}]}
            args = dict(order_id=order["id"], target="new", actor="operator", note="old offline", roster=roster)
            online = {"agents": [{"agent": "new", "state": "online", "active_assignments": []}]}
            with patch.object(router.presence, "snapshot", return_value=online):
                self.assertEqual(router.reroute(root, **args)["status"], "PLAN_ONLY")
                self.assertEqual(commander._orders(root)[0]["target"], "old")
                self.assertEqual(router.reroute(root, **args, apply=True)["status"], "REROUTED")
            persisted = commander._orders(root)
            self.assertEqual(len(persisted), 1)
            self.assertEqual(persisted[0]["event_id"], order["event_id"])
            self.assertEqual(persisted[0]["attempts"][-1]["receipt"]["previous_target"], "old")
            with patch.object(commander.registry, "profile") as profile:
                self.assertEqual(commander.dispatch(root, order=order)["target"], "new")
                profile.assert_not_called()

    def test_reroute_rejects_claimed_offline_or_input_history(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"]
            head = improvement.status(root, identity)["events"][-1]["sha256"]
            order = commander.submit_order(root, target="old", task="improvement:" + identity,
                text="verify", sender="router", event_id="EVT-IMPROVE-" + head[:24].upper())
            args = dict(order_id=order["id"], target="new", actor="operator", note="handoff",
                        roster={"agents": [{"agent": "new", "actions": ["validate"]}]}, apply=True)
            with patch.object(router.presence, "snapshot", return_value={"agents": []}):
                with self.assertRaisesRegex(ValueError, "online"):
                    router.reroute(root, **args)
            lease = improvement_work.claim(root, identity, actor="old")
            with self.assertRaisesRegex(ValueError, "claimed"):
                router.reroute(root, **args)
            improvement_work.finish(root, identity, actor="old", token=lease["token"], release=True)
            commander._record_attempt(root, order, state="INPUT_SUBMITTED")
            commander._record_attempt(root, order, state="DEFERRED")
            with self.assertRaisesRegex(ValueError, "started"):
                router.reroute(root, **args)
            self.assertEqual(commander._orders(root)[0]["target"], "old")

    def test_ack_without_claim_keeps_slot_until_stage_changes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            identities = [improvement.propose(root, dict(author="a", change=change, acceptance="c",
                rollback="d", scope="e", evidence=[str(proof)]))["id"] for change in ("first", "second")]
            roster = {"agents": [{"agent": "worker", "actions": ["validate"]}]}
            online = {"agents": [{"agent": "worker", "state": "online", "active_assignments": []}]}
            with patch.object(router.presence, "snapshot", return_value=online):
                self.assertEqual(router.schedule(root, roster, apply=True)["orders_queued"], 1)
                order = commander._orders(root)[0]
                commander._record_attempt(root, order, state="AGENT_ACK", note="fixture only")
                before = len(commander._orders(root))
                self.assertEqual(router.schedule(root, roster, apply=True)["orders_queued"], 0)
                self.assertEqual(len(commander._orders(root)), before)
                identity = order["task"].split(":", 1)[1]
                improvement.advance(root, identity, state="VALIDATED", actor="worker",
                                    evidence=[str(proof)], note="fixture validation")
                self.assertEqual(router.schedule(root, roster, apply=True)["orders_queued"], 1)
                self.assertEqual(len(commander._orders(root)), before + 1)

    def test_missing_progress_and_uncertain_delivery_keep_slot(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            order = commander.submit_order(root, target="worker", task="improvement:" + "a" * 24,
                                            text="verify", sender="router")
            commander._record_attempt(root, order, state="AGENT_ACK")
            self.assertTrue(router._occupies_slot(root, order))
            for state in ("FAILED", "CEASED", "INPUT_SUBMITTED", "DEFERRED"):
                self.assertTrue(router._occupies_slot(root, {**order, "state": state}))
            self.assertFalse(router._occupies_slot(root, {**order, "state": "RETIRED"}))
            self.assertFalse(router._occupies_slot(root, {**order, "task": "ordinary"}))

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
