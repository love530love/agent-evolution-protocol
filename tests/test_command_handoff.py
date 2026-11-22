"""Tests for the command-handoff state machine.

Every test runs against a throwaway temporary coord_root so no real workspace
or WIP file is ever touched.
"""
import tempfile
import unittest

from agent_evolution_protocol.federation import command_handoff as ch


class CommandHandoffTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    # 1. normal closed loop: ACTIVE -> YIELDED -> takeover -> ACTIVE
    def test_happy_path_yield_then_takeover(self):
        ch.claim(self.root, agent="agent-a", role="commander",
                 capability="command", evidence=["boot:lease-1"])
        ch.volunteer(self.root, from_agent="agent-b", role="commander",
                     capability="command recon", reason="standby",
                     evidence=["card:agent-b"])
        yielded = ch.yield_role(self.root, from_agent="agent-a", role="commander",
                                signal="quota_exhausted", evidence=["429:quota"])
        self.assertEqual(yielded["state"], "YIELDED")
        self.assertTrue(yielded["takeover_eligible"])
        taken = ch.takeover(self.root, to_agent="agent-b", role="commander",
                            evidence=["receipt:handoff-1"])
        self.assertEqual(taken["state"], "ACTIVE")
        self.assertEqual(taken["from"], "agent-a")
        self.assertEqual(taken["to"], "agent-b")
        self.assertEqual(taken["transition"], ["YIELDED", "TAKING_OVER", "ACTIVE"])
        view = ch.status(self.root)["roles"]["commander"]
        self.assertEqual(view["holder"], "agent-b")
        self.assertEqual(view["state"], "ACTIVE")

    # 2. a weak agent without a covering capability cannot take over
    def test_weak_agent_without_capability_cannot_takeover(self):
        ch.claim(self.root, agent="agent-a", role="builder",
                 capability="build", evidence=["boot"])
        weak = ch.volunteer(self.root, from_agent="weak-b", role="builder",
                            capability="", reason="help", evidence=["weak-card"])
        self.assertFalse(weak["eligible"])
        ch.yield_role(self.root, from_agent="agent-a", role="builder",
                      signal="time_limit", evidence=["deadline"])
        with self.assertRaises(ValueError):
            ch.takeover(self.root, to_agent="weak-b", role="builder",
                        evidence=["attempt"])

    # 3. evidence is mandatory everywhere (fail-closed)
    def test_evidence_required(self):
        with self.assertRaises(ValueError):
            ch.volunteer(self.root, from_agent="x", role="scout",
                         capability="recon", reason="r", evidence=[])
        with self.assertRaises(ValueError):
            ch.volunteer(self.root, from_agent="x", role="scout",
                         capability="recon", reason="r", evidence="   ")
        ch.claim(self.root, agent="a", role="scout",
                 capability="recon", evidence=["boot"])
        with self.assertRaises(ValueError):
            ch.yield_role(self.root, from_agent="a", role="scout",
                          signal="usage_limit", evidence=None)
        ch.yield_role(self.root, from_agent="a", role="scout",
                      signal="usage_limit", evidence=["limit-hit"])
        ch.volunteer(self.root, from_agent="b", role="scout",
                     capability="recon", reason="r", evidence=["b-card"])
        with self.assertRaises(ValueError):
            ch.takeover(self.root, to_agent="b", role="scout", evidence=())

    # 4. an illegal signal is rejected (token_exhausted is NOT in capacity set)
    def test_illegal_signal_rejected(self):
        ch.claim(self.root, agent="a", role="commander",
                 capability="command", evidence=["boot"])
        for signal in ("token_exhausted", "random", "", None):
            with self.assertRaises(ValueError):
                ch.yield_role(self.root, from_agent="a", role="commander",
                              signal=signal, evidence=["limit"])

    # 5. single writer: exactly one ACTIVE holder per role
    def test_single_active_holder(self):
        ch.claim(self.root, agent="a", role="github",
                 capability="repo_intel", evidence=["boot"])
        with self.assertRaises(ValueError):
            ch.claim(self.root, agent="b", role="github",
                     capability="repo_intel", evidence=["boot2"])
        ch.volunteer(self.root, from_agent="b", role="github",
                     capability="repo_intel", reason="r", evidence=["b-card"])
        # takeover of an ACTIVE role must be rejected (no double ACTIVE)
        with self.assertRaises(ValueError):
            ch.takeover(self.root, to_agent="b", role="github", evidence=["attempt"])

    # 6. unknown roles are rejected everywhere (fail-closed, no guessing)
    def test_unknown_role_rejected(self):
        with self.assertRaises(ValueError):
            ch.claim(self.root, agent="a", role="wizard",
                     capability="magic", evidence=["boot"])
        with self.assertRaises(ValueError):
            ch.volunteer(self.root, from_agent="a", role="wizard",
                         capability="magic", reason="r", evidence=["card"])
        with self.assertRaises(ValueError):
            ch.yield_role(self.root, from_agent="a", role="wizard",
                          signal="quota_exhausted", evidence=["e"])
        with self.assertRaises(ValueError):
            ch.takeover(self.root, to_agent="a", role="wizard", evidence=["e"])

    # 7. replaying an identical takeover is idempotent
    def test_duplicate_takeover_idempotent(self):
        ch.claim(self.root, agent="a", role="commander",
                 capability="command", evidence=["boot"])
        ch.volunteer(self.root, from_agent="b", role="commander",
                     capability="command", reason="r", evidence=["b-card"])
        ch.yield_role(self.root, from_agent="a", role="commander",
                      signal="credit_exhausted", evidence=["limit"])
        first = ch.takeover(self.root, to_agent="b", role="commander",
                            evidence=["receipt"])
        second = ch.takeover(self.root, to_agent="b", role="commander",
                             evidence=["receipt"])
        self.assertEqual(first["handoff_id"], second["handoff_id"])
        self.assertEqual(first["seq"], second["seq"])
        self.assertTrue(second["idempotent"])
        view = ch.status(self.root)
        self.assertEqual(len(view["roles"]["commander"]["handoffs"]), 1)
        self.assertEqual(view["seq"], first["seq"])  # replay appended no event
        self.assertEqual(view["roles"]["commander"]["holder"], "b")

    # 8. status exposes the full, verifiable evidence chain
    def test_status_evidence_chain_complete(self):
        ch.claim(self.root, agent="a", role="scout",
                 capability="recon", evidence=["boot"])
        ch.volunteer(self.root, from_agent="b", role="scout",
                     capability="recon", reason="ready", evidence=["b-card"])
        ch.yield_role(self.root, from_agent="a", role="scout",
                      signal="usage_limit", evidence=["cap"])
        ch.takeover(self.root, to_agent="b", role="scout", evidence=["receipt"])
        view = ch.status(self.root)
        self.assertTrue(view["chain_valid"])
        kinds = [event["kind"] for event in view["events"]]
        self.assertEqual(kinds, ["claim", "volunteer", "yield", "takeover"])
        scout = view["roles"]["scout"]
        self.assertEqual(len(scout["evidence"]), 4)
        self.assertEqual(scout["candidates"][0]["agent"], "b")
        self.assertEqual(scout["candidates"][0]["evidence"], ["b-card"])
        self.assertEqual(len(scout["handoffs"]), 1)

    # 9. extension via register_role, and warning signal -> LIMITED
    def test_register_role_and_warning_signal(self):
        ext = ch.register_role(self.root, role="analyst", capability="analysis")
        self.assertTrue(ext["ok"])
        again = ch.register_role(self.root, role="analyst", capability="analysis")
        self.assertTrue(again["idempotent"])
        ch.claim(self.root, agent="a", role="analyst",
                 capability="analysis", evidence=["boot"])
        yielded = ch.yield_role(self.root, from_agent="a", role="analyst",
                                signal="credit_warning", evidence=["warn"])
        self.assertEqual(yielded["state"], "LIMITED")
        self.assertTrue(yielded["takeover_eligible"])


if __name__ == "__main__":
    unittest.main()
