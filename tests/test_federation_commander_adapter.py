"""The adapter that connects the scheduler to real desktop sessions.

Nothing here touches a window: ``deliver`` only queues, and that is the property
under test.  A unit test that opened a client would be testing the operator's
desktop, not the adapter.
"""
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_evolution_protocol.coordination import commander
from agent_evolution_protocol.federation import assignment
from agent_evolution_protocol.federation.commander_adapter import (
    CommanderAdapter, TERMINAL_ORDER_STATES)


def _set_state(coord_root, order_id, state, note=""):
    path = Path(coord_root) / "commander" / "inbox" / f"{order_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"] = state
    if note:
        payload["note"] = note
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


class CommanderAdapterTests(unittest.TestCase):
    WORKSPACE = "W-0123456789abcdef01234567"

    def setUp(self):
        self._temp = TemporaryDirectory()
        self.coord = self._temp.name
        self.addCleanup(self._temp.cleanup)

    def _adapter(self, agent="alpha", **kwargs):
        return CommanderAdapter(agent, coord_root=self.coord,
                                assignment=assignment.render, **kwargs)

    def test_deliver_queues_without_touching_a_window(self):
        adapter = self._adapter()
        result = adapter.deliver({"id": "T1", "requirement": "跑通派工",
                                  "workspace_id": self.WORKSPACE})
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["order_id"])
        orders = commander._orders(self.coord)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["state"], "DEPOSITED")
        # The instruction has to be executable, not a notification.
        self.assertIn("AEP-ACK:" + orders[0]["event_id"], orders[0]["text"])
        self.assertIn("coord-task-state", orders[0]["text"])

    def test_second_deliver_for_a_live_task_does_not_duplicate(self):
        adapter = self._adapter()
        first = adapter.deliver({"id": "T1", "requirement": "x", "workspace_id": self.WORKSPACE})
        second = adapter.deliver({"id": "T1", "requirement": "x", "workspace_id": self.WORKSPACE})
        self.assertEqual(first["order_id"], second["order_id"])
        self.assertEqual(len(commander._orders(self.coord)), 1)

    def test_terminal_order_allows_a_new_delivery(self):
        adapter = self._adapter()
        first = adapter.deliver({"id": "T1", "requirement": "x", "workspace_id": self.WORKSPACE})
        _set_state(self.coord, first["order_id"], "CEASED", note="dead")
        self.assertIsNone(adapter.live_order("T1"))
        second = adapter.deliver({"id": "T1", "requirement": "x", "workspace_id": self.WORKSPACE})
        self.assertNotEqual(first["order_id"], second["order_id"])
        self.assertEqual(len(commander._orders(self.coord)), 2)

    def test_heartbeat_counts_the_deposit_as_progress(self):
        adapter = self._adapter()
        now = datetime.now(timezone.utc)
        self.assertIsNone(adapter.heartbeat(now)["last_progress_at"])
        adapter.deliver({"id": "T1", "requirement": "x", "workspace_id": self.WORKSPACE})
        beat = adapter.heartbeat(now)
        self.assertIsNotNone(beat["last_progress_at"])
        # A task queued a second ago is not a stalled agent.
        stamp = datetime.fromisoformat(beat["last_progress_at"])
        self.assertLess(abs((now - stamp).total_seconds()), 60)

    def test_heartbeat_without_work_is_not_a_dead_agent(self):
        beat = self._adapter().heartbeat(datetime.now(timezone.utc))
        self.assertTrue(beat["alive"])
        self.assertEqual(beat["orders"], 0)

    def test_observe_reports_counts_not_screens(self):
        adapter = self._adapter()
        first = adapter.deliver({"id": "T1", "requirement": "x", "workspace_id": self.WORKSPACE})
        adapter.deliver({"id": "T2", "requirement": "y", "workspace_id": self.WORKSPACE})
        _set_state(self.coord, first["order_id"], "AGENT_ACK")
        observed = adapter.observe()
        self.assertEqual(observed["orders"], 2)
        self.assertEqual(observed["acked"], 1)
        self.assertEqual(observed["pending"], 1)

    def test_failure_signals_come_from_durable_records(self):
        adapter = self._adapter()
        order = adapter.deliver({"id": "T1", "requirement": "x", "workspace_id": self.WORKSPACE})
        _set_state(self.coord, order["order_id"], "CEASED", note="ceasefire")
        self.assertEqual(adapter.failure_signal("T1"), "adapter_crashed")
        _set_state(self.coord, order["order_id"], "FAILED", note="EXPIRED")
        self.assertEqual(adapter.failure_signal("T1"), "token_exhausted")

    def test_a_deferred_order_is_not_a_failure(self):
        adapter = self._adapter()
        order = adapter.deliver({"id": "T1", "requirement": "x", "workspace_id": self.WORKSPACE})
        _set_state(self.coord, order["order_id"], "DEFERRED", note="CLIENT_LAUNCHED")
        self.assertEqual(adapter.failure_signal("T1"), "")
        self.assertIsNotNone(adapter.live_order("T1"))

    def test_invalid_task_is_refused_not_queued(self):
        result = self._adapter().deliver({"requirement": "no id"})
        self.assertEqual(result["status"], "refused")
        self.assertEqual(commander._orders(self.coord), [])

    def test_capabilities_must_be_declared(self):
        with self.assertRaises(ValueError):
            CommanderAdapter("alpha", coord_root=self.coord, capabilities=())
        self.assertEqual(self._adapter().capabilities(), {"execute"})

    def test_terminal_states_are_the_documented_set(self):
        self.assertEqual(TERMINAL_ORDER_STATES,
                         frozenset({"AGENT_ACK", "FAILED", "CEASED", "RETIRED"}))


if __name__ == "__main__":
    unittest.main()
