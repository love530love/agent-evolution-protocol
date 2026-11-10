"""Switching surface is a retry, not a new decision.

The policy came from the operator: "换表面投递不需要重新征得同意，因为 UAC 提权弹窗和
任务执行过程中重新征得同意，都是任务阻断行为".  Both are prompts in the middle of an
unattended run, and a prompt is a stop.

The switch is only allowed at one point in the delivery path -- immediately after
a receipt that *proves nothing was typed*, because UIPI refused the input before
it reached the window.  That proof is what separates a retry from a duplicate: a
switch after an uncertain send would send the message twice, and no policy
decision makes that acceptable.  ``test_switch_is_refused_after_an_uncertain_send``
pins that boundary.

``continuity`` is the gate on *where*: ``continues is True`` means the
conversation marker matches across surfaces.  ``None`` means there was nothing
to compare, and it is treated as a refusal -- two unlabelled threads are
indistinguishable, and guessing is how a message lands in the wrong one.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agent_evolution_protocol.coordination import commander, session_fingerprint

BLOCKED = "workbuddy3"
OTHER = "workbuddy_ai"


def _order(root: Path) -> dict:
    return commander.submit_order(root, target=BLOCKED, task="AEP-SURFACE-SWITCH",
                                  text="continue here", sender="workbuddyai")


def _discovery(*agents: str) -> dict:
    return {"clients": [
        {"agent": name, "windows": [{"pid": 100 + index, "title": f"{name} window"}],
         "unique": True, "anchored": False, "out_of_scope": ""}
        for index, name in enumerate(agents)
    ]}


class DeferralBudgetTests(unittest.TestCase):
    def test_an_environmental_deferral_does_not_spend_the_retry_budget(self):
        order = {"attempts": [
            {"state": "DEFERRED", "receipt": {"status": "EXECUTOR_TOO_LOW"}},
            {"state": "DEFERRED", "receipt": {"status": "EXECUTOR_TOO_LOW"}},
            {"state": "DEFERRED", "receipt": {"status": "AGENT_BUSY"}},
        ]}
        self.assertEqual(commander._deferred_count(order), 1)

    def test_an_ordinary_deferral_still_counts(self):
        order = {"attempts": [{"state": "DEFERRED", "receipt": {"status": "NO_WINDOW"}}] * 3}
        self.assertEqual(commander._deferred_count(order), 3)

    def test_the_environmental_set_is_named(self):
        self.assertIn("EXECUTOR_TOO_LOW", commander.ENVIRONMENTAL_DEFERRALS)


class ContinuationCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.order = _order(self.root)

    def _patches(self, *, continuity=True, discovery=None):
        return [
            mock.patch.object(session_fingerprint, "read",
                              return_value={"conversation": "FlagGems-sglang"}),
            mock.patch.object(session_fingerprint, "merged_profile", return_value={}),
            mock.patch.object(session_fingerprint, "ui_profile", return_value={}),
            mock.patch.object(session_fingerprint, "observe",
                              return_value={"conversation": "FlagGems-sglang"}),
            mock.patch.object(session_fingerprint, "continuity",
                              return_value={"continues": continuity}),
            mock.patch.object(commander.registry, "discover",
                              return_value=discovery or _discovery(OTHER)),
            mock.patch.object(commander.registry, "profile", return_value={}),
        ]

    def _run(self, **kwargs):
        patches = self._patches(**kwargs)
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return commander._continuation_surfaces(self.root, self.order, blocked_target=BLOCKED)

    def test_a_continuing_surface_is_a_candidate(self):
        found = self._run()
        self.assertEqual([item["agent"] for item in found], [OTHER])

    def test_no_recorded_surface_means_no_candidate(self):
        with mock.patch.object(session_fingerprint, "read", return_value=None):
            self.assertEqual(
                commander._continuation_surfaces(self.root, self.order, blocked_target=BLOCKED), [])

    def test_an_inconclusive_marker_is_a_refusal_not_a_guess(self):
        self.assertEqual(self._run(continuity=None), [])

    def test_the_blocked_target_is_never_its_own_replacement(self):
        self.assertEqual(self._run(discovery=_discovery(BLOCKED)), [])

    def test_an_out_of_scope_surface_is_never_a_candidate(self):
        discovery = _discovery(OTHER)
        discovery["clients"][0]["out_of_scope"] = "browser tabs are not a surface"
        self.assertEqual(self._run(discovery=discovery), [])


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.order = _order(self.root)

    def _switch(self, **kwargs):
        patches = [
            mock.patch.object(commander, "_continuation_surfaces",
                              return_value=kwargs.get("candidates", [{
                                  "agent": OTHER, "pid": 101, "window_title": "WorkBuddy AI",
                                  "continuity": {"continues": True}, "observed": {}}])),
            mock.patch.object(commander, "_dispatch", return_value={"state": "WINDOW_FOUND"}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return commander._switch_surface(
            self.root, dict(self.order), blocked_target=BLOCKED, transport="uia",
            receipt={"status": "EXECUTOR_TOO_LOW"})

    def test_the_order_continues_on_the_other_surface(self):
        result = self._switch()
        self.assertEqual(result["state"], "WINDOW_FOUND")
        persisted = commander._persisted_order(self.root, self.order["id"])
        self.assertEqual(persisted["target"], OTHER)
        last = persisted["attempts"][-1]
        self.assertIn("SURFACE_SWITCHED", last["note"])
        # The evidence travels with the order: who it moved to, and why it was
        # allowed to move without asking.
        evidence = last["receipt"]["surface_switch"]
        self.assertEqual(evidence["from"], BLOCKED)
        self.assertEqual(evidence["to"], OTHER)
        self.assertEqual(evidence["blocked_receipt_status"], "EXECUTOR_TOO_LOW")

    def test_nowhere_to_go_returns_none_and_leaves_the_order_alone(self):
        self.assertIsNone(self._switch(candidates=[]))
        persisted = commander._persisted_order(self.root, self.order["id"])
        self.assertEqual(persisted["target"], BLOCKED)

    def test_only_one_switch_per_order(self):
        self.assertIsNotNone(self._switch())
        again = commander._switch_surface(
            self.root, commander._persisted_order(self.root, self.order["id"]),
            blocked_target=OTHER, transport="uia", receipt={"status": "EXECUTOR_TOO_LOW"})
        self.assertIsNone(again)


class WiringTests(unittest.TestCase):
    """The switch has to actually be reachable from the delivery path."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.order = _order(self.root)

    def _dispatch_with(self, receipt):
        profile = self.root / "ui_profiles" / f"{BLOCKED}.json"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text("{}", encoding="utf-8")
        window = {"pid": 4242, "title": "WorkBuddy", "is_anchor": True}
        discovery = {"clients": [{"agent": BLOCKED, "windows": [window],
                                  "unique": True, "anchored": True, "out_of_scope": ""}]}
        switch = mock.Mock(return_value={"state": "WINDOW_FOUND", "switched": True})
        patches = [
            mock.patch.object(commander.registry, "profile", return_value={"agent": BLOCKED}),
            mock.patch.object(commander.registry, "discover", return_value=discovery),
            mock.patch.object(session_fingerprint, "merged_profile", return_value={}),
            mock.patch.object(session_fingerprint, "ui_profile", return_value={}),
            mock.patch.object(session_fingerprint, "observe", return_value={}),
            mock.patch.object(session_fingerprint, "compare", return_value={"match": True}),
            mock.patch.object(commander, "_gate_refusal", return_value=None),
            mock.patch.object(commander, "_pick_transport", return_value=("uia", {})),
            mock.patch.object(commander, "_profiled_deliver", return_value=receipt),
            mock.patch.object(commander, "_switch_surface", switch),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return commander._dispatch(self.root, order=dict(self.order)), switch

    def test_a_blocked_integrity_level_triggers_a_switch(self):
        result, switch = self._dispatch_with({"status": "EXECUTOR_TOO_LOW"})
        switch.assert_called_once()
        self.assertTrue(result.get("switched"))

    def test_an_uncertain_send_never_switches(self):
        # No receipt that proves "nothing was typed" -> no switch.  A second
        # attempt after a possible send is a duplicate, whatever the policy.
        result, switch = self._dispatch_with({"status": "NO_RECEIPT", "returncode": 1})
        switch.assert_not_called()
        self.assertEqual(result["state"], "CEASED")

    def test_a_busy_target_does_not_switch_either(self):
        # Being busy is about the target, not the executor; the conversation is
        # still where it was and waiting is correct.
        result, switch = self._dispatch_with({"status": "AGENT_BUSY"})
        switch.assert_not_called()
        self.assertEqual(result["state"], "DEFERRED")


if __name__ == "__main__":
    unittest.main()
