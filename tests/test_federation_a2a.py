"""The adapter boundary must survive a real process, not just a fake object.

These tests spawn ``examples/a2a_agent_stub.py``. That is the whole point: an
in-process fake cannot show that a peer which dies mid-conversation, or goes
silent, is reported as a failure the kernel can act on.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

from agent_evolution_protocol.federation import diagnosis, engine
from agent_evolution_protocol.federation.a2a import A2AAdapter
from agent_evolution_protocol.federation.adapter import FakeAdapter


NOW = datetime(2026, 10, 31, 4, 0, tzinfo=timezone.utc)
STUB = Path(__file__).resolve().parents[1] / "examples" / "a2a_agent_stub.py"


def stub(agent="alpha", *, capabilities="code", outcome="accepted", delay=0.0,
         die_after=0, silent=False):
    args = [str(STUB), "--agent", agent, "--capabilities", capabilities,
            "--outcome", outcome, "--delay", str(delay), "--die-after", str(die_after)]
    if silent:
        args.append("--silent")
    return args


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.opened = []

    def tearDown(self):
        for adapter in self.opened:
            adapter.close()

    def make(self, *, timeout=5.0, restart=True, agent="alpha", **stub_kwargs):
        adapter = A2AAdapter(agent, args=stub(agent=agent, **stub_kwargs),
                             timeout=timeout, restart=restart)
        self.opened.append(adapter)
        return adapter

    def test_capabilities_come_from_the_real_process(self):
        adapter = self.make(capabilities="code,review")
        self.assertEqual(adapter.capabilities(), {"code", "review"})

    def test_accepted_delivery_round_trips(self):
        adapter = self.make()
        result = adapter.deliver({"id": "t1", "requirement": "code"})
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["signal"], "")

    def test_a_named_failure_survives_the_boundary(self):
        adapter = self.make(outcome="token_exhausted")
        result = adapter.deliver({"id": "t1", "requirement": "code"})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["signal"], "token_exhausted")

    def test_a_refusal_has_no_signal(self):
        adapter = self.make(outcome="refused")
        result = adapter.deliver({"id": "t1", "requirement": "code"})
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["signal"], "")

    def test_a_silent_peer_times_out_instead_of_hanging(self):
        adapter = self.make(silent=True, timeout=0.3)
        result = adapter.deliver({"id": "t1", "requirement": "code"})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["signal"], "adapter_timeout")

    def test_a_dead_peer_is_reported_as_crashed(self):
        adapter = self.make(die_after=1, restart=False)
        self.assertEqual(adapter.capabilities(), {"code"})
        result = adapter.deliver({"id": "t1", "requirement": "code"})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["signal"], "adapter_crashed")

    def test_observe_reports_what_the_process_did(self):
        adapter = self.make()
        adapter.deliver({"id": "t1", "requirement": "code"})
        seen = adapter.observe()
        self.assertEqual(seen["state"], "worked")
        self.assertEqual(seen["deliveries"], 1)


class DiagnosisCoversTransportSignals(unittest.TestCase):
    def test_a_dead_or_silent_peer_is_recoverable(self):
        for signal in ("adapter_crashed", "adapter_timeout"):
            with self.subTest(signal=signal):
                decision = diagnosis.classify(signal)
                self.assertEqual(decision["action"], "switch_agent")
                self.assertFalse(decision["needs_human"])

    def test_a_protocol_violation_still_escalates(self):
        decision = diagnosis.classify("adapter_protocol_error")
        self.assertEqual(decision["action"], "escalate")
        self.assertTrue(decision["needs_human"])


class EngineOverARealProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = engine.create(self.root, goal="ship it", acceptance=["tests pass"],
                                   moment=NOW)
        self.opened = []

    def tearDown(self):
        for adapter in self.opened:
            adapter.close()
        self.tmp.cleanup()

    def a2a(self, *, timeout=5.0, restart=True, agent="alpha", **stub_kwargs):
        adapter = A2AAdapter(agent, args=stub(agent=agent, **stub_kwargs),
                             timeout=timeout, restart=restart)
        self.opened.append(adapter)
        return adapter

    def test_a_real_exhausted_agent_is_replaced(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code", moment=NOW)
        adapters = [self.a2a(outcome="token_exhausted"),
                    FakeAdapter("beta", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW, moment=NOW)

        task = self.state["tasks"]["t1"]
        self.assertEqual(task["owner"], "beta")
        self.assertEqual(task["signal"], "token_exhausted")
        self.assertEqual(self.state["agents"]["alpha"]["state"], "archived")

    def test_a_real_crash_mid_conversation_hands_the_task_over(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code", moment=NOW)
        adapters = [self.a2a(die_after=1, restart=False),
                    FakeAdapter("beta", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW, moment=NOW)

        task = self.state["tasks"]["t1"]
        self.assertEqual(task["owner"], "beta")
        self.assertEqual(task["signal"], "adapter_crashed")


if __name__ == "__main__":
    unittest.main()
