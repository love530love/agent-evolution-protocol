"""The heartbeat: one input, several agents, nobody relaying messages by hand.

These tests drive the loop with durable records only.  No window is opened and
no client is launched -- what is under test is whether the supervisor notices
that work finished, died, or cannot safely start.
"""
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_evolution_protocol.coordination import claim_conflicts, commander
from agent_evolution_protocol.federation import engine, intake, supervisor


def _report(coord, task_id, agent, phase="done", **extra):
    folder = Path(coord) / "task_states"
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"task": task_id, "owner": agent, "phase": phase, "evidence": []}
    payload.update(extra)
    (folder / f"{task_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def _set_order(coord, order_id, state, note="", session_id=""):
    path = Path(coord) / "commander" / "inbox" / f"{order_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"] = state
    if note:
        payload["note"] = note
    if session_id:
        payload["session_id"] = session_id
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _age_order(coord, order_id, seconds):
    """Backdate the send so a hold's age can be asserted without waiting."""
    path = Path(coord) / "commander" / "inbox" / f"{order_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    created = datetime.fromisoformat(str(payload["created_at"]).replace("Z", "+00:00"))
    payload["created_at"] = (created - timedelta(seconds=seconds)).isoformat()
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self._temp = TemporaryDirectory()
        self.root = Path(self._temp.name) / "state"
        self.coord = str(Path(self._temp.name) / "coordination")
        self.addCleanup(self._temp.cleanup)

    def _intake(self, agents=("alpha", "beta"), shape="reviewed", text="跑通 README"):
        receipt = intake.intake(self.root, self.coord, text=text, agents=list(agents),
                                shape=shape, sender="kernel")
        adapters = supervisor.build_adapters(coord_root=self.coord, agents=list(agents),
                                             workspace_id=receipt["workspace"])
        return receipt, adapters

    def test_one_input_starts_the_first_wave_without_a_conversation(self):
        receipt, adapters = self._intake()
        self.assertEqual(receipt["added"], ["execute", "review"])
        self.assertEqual(len(receipt["started"]), 1)
        state = engine.load(self.root, receipt["workspace"])
        self.assertEqual(state["tasks"]["execute"]["owner"], "alpha")
        self.assertEqual(state["tasks"]["review"]["state"], "pending")
        self.assertEqual(len(commander._orders(self.coord)), 1)

    def test_completion_relays_to_an_independent_reviewer_in_the_same_step(self):
        receipt, adapters = self._intake()
        _report(self.coord, "execute", "alpha")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertIn({"task": "execute", "action": "complete", "artifact": ""},
                      result["closed"])
        self.assertEqual(result["tasks"]["execute"]["state"], "done")
        # Independence: the reviewer must not be the executor.
        self.assertEqual(result["tasks"]["review"]["owner"], "beta")
        self.assertEqual(result["tasks"]["review"]["state"], "running")

    def test_the_whole_chain_settles_without_further_input(self):
        receipt, adapters = self._intake()
        _report(self.coord, "execute", "alpha")
        supervisor.step(self.root, self.coord, receipt["workspace"], adapters, dispatch=False)
        _report(self.coord, "review", "beta")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertTrue(supervisor._settled(result))
        self.assertEqual(result["tasks"]["review"]["state"], "done")

    def test_a_dead_delivery_is_replaced_instead_of_waited_out(self):
        receipt, adapters = self._intake(agents=("alpha", "beta"), shape="single")
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "CEASED", note="client died")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertEqual(result["closed"][0]["signal"], "adapter_crashed")
        state = engine.load(self.root, receipt["workspace"])
        task = state["tasks"]["execute"]
        # switch_agent: the owner changed and a handoff packet was recorded.
        self.assertEqual(task["owner"], "beta")
        self.assertEqual(task["state"], "running")
        self.assertEqual(len(state["handoffs"]), 1)

    def test_a_blocked_report_is_treated_as_a_refusal(self):
        receipt, adapters = self._intake(agents=("alpha", "beta"), shape="single")
        _report(self.coord, "execute", "alpha", phase="blocked",
                blocked_reason="缺少凭据")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertEqual(result["closed"][0]["action"], "blocked")

    def test_work_that_cannot_be_written_is_not_left_running(self):
        claim_conflicts.claim(self.coord, agent="gamma", task="OTHER", paths=["docs/owned.md"])
        receipt = intake.intake(self.root, self.coord, text="改 docs/owned.md",
                                agents=["alpha"], shape="single", sender="kernel",
                                steps=[{"id": "execute", "requirement": "改 docs/owned.md",
                                        "deps": [], "paths": ["docs/owned.md"]}])
        adapters = supervisor.build_adapters(coord_root=self.coord, agents=["alpha"],
                                             workspace_id=receipt["workspace"])
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertFalse(result["claims"][0]["claimed"])
        self.assertEqual(result["tasks"]["execute"]["state"], "blocked")

    def test_serve_stops_at_the_tick_budget(self):
        receipt, adapters = self._intake(agents=("alpha",), shape="single")
        ticks = supervisor.serve(self.root, self.coord, receipt["workspace"], adapters,
                                 interval=1.0, max_ticks=1, dispatch=False,
                                 sleep=lambda _: self.fail("serve must not sleep past its budget"))
        self.assertEqual(ticks, 1)

    def test_serve_refuses_a_busy_loop(self):
        with self.assertRaises(ValueError):
            supervisor.serve(self.root, self.coord, "W-0123456789abcdef01234567", [],
                             interval=0.1)

    def test_adapters_need_agents_and_declared_capabilities(self):
        with self.assertRaises(ValueError):
            supervisor.build_adapters(coord_root=self.coord, agents=[])
        adapters = supervisor.build_adapters(coord_root=self.coord, agents=["alpha"],
                                             capabilities={"alpha": ["execute", "review"]})
        self.assertEqual(adapters[0].capabilities(), {"execute", "review"})


class AckGateTests(unittest.TestCase):
    """No ACK, no downstream progress -- for orders that were actually sent."""

    def setUp(self):
        self._temp = TemporaryDirectory()
        self.root = Path(self._temp.name) / "state"
        self.coord = str(Path(self._temp.name) / "coordination")
        self.addCleanup(self._temp.cleanup)

    def _intake(self):
        receipt = intake.intake(self.root, self.coord, text="跑通 README",
                                agents=["alpha", "beta"], shape="reviewed", sender="kernel")
        adapters = supervisor.build_adapters(coord_root=self.coord, agents=["alpha", "beta"],
                                             workspace_id=receipt["workspace"])
        return receipt, adapters

    def test_a_sent_order_without_an_ack_does_not_close_the_task(self):
        receipt, adapters = self._intake()
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE")
        _report(self.coord, "execute", "alpha")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertEqual(result["closed"], [])
        self.assertEqual([row["action"] for row in result["held"]], ["hold_unacked"])
        self.assertEqual(result["held"][0]["state"], "sent_unacked")
        self.assertEqual(result["tasks"]["execute"]["state"], "running")
        # The reviewer must not start on work nobody accepted.
        self.assertEqual(result["tasks"]["review"]["state"], "pending")

    def test_the_same_task_closes_once_the_ack_arrives(self):
        from agent_evolution_protocol.coordination import transcript_bus
        receipt, adapters = self._intake()
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        _report(self.coord, "execute", "alpha")
        held = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                               dispatch=False)
        self.assertEqual(held["closed"], [])
        transcript_bus.publish(self.coord, agent="alpha",
                               text="AEP-ACK:" + order["event_id"],
                               session_id="4242",
                               role="assistant", direction="incoming")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertIn({"task": "execute", "action": "complete", "artifact": ""},
                      result["closed"])
        self.assertEqual(result["held"], [])

    def test_an_order_that_was_never_sent_is_not_held_for_an_ack(self):
        # Nothing reached a conversation, so nothing can acknowledge it; holding
        # here would be waiting for a reply to a message nobody delivered.
        receipt, adapters = self._intake()
        _report(self.coord, "execute", "alpha")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertEqual(result["held"], [])
        self.assertEqual(result["tasks"]["execute"]["state"], "done")

    def test_an_abandoned_order_blocks_the_task_and_downstream_stays_pending(self):
        receipt, adapters = self._intake()
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        _report(self.coord, "execute", "alpha")
        first = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                dispatch=False)
        self.assertEqual([row["action"] for row in first["held"]], ["hold_unacked"])

        commander.abandon(self.coord, order["id"], actor="love",
                          reason="the client is wedged; re-planning tomorrow")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertEqual([row["action"] for row in result["closed"]], ["abandoned"])
        self.assertEqual(result["held"], [])
        # Blocked, never done: abandoning a delivery is not evidence of the work.
        self.assertEqual(result["tasks"]["execute"]["state"], "blocked")
        # And the existing propagation does the rest: the dependent is marked
        # blocked with the reason, rather than left pending forever with no
        # explanation of why it will never start.
        self.assertEqual(result["tasks"]["review"]["state"], "blocked")
        self.assertIn("blocked by", engine.load(self.root, receipt["workspace"])
                      ["tasks"]["review"]["note"])

    def test_a_hold_reports_its_age_so_an_unbounded_wait_is_visible(self):
        receipt, adapters = self._intake()
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        _age_order(self.coord, order["id"], 3600)
        _report(self.coord, "execute", "alpha")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        row = result["held"][0]
        self.assertEqual(row["verdict"], "hold")
        self.assertGreaterEqual(row["unacked_for_seconds"], 3600)
        self.assertIsNone(row["limit_seconds"])
        self.assertFalse(row["over_limit"])

    def test_crossing_the_hold_limit_reports_it_without_advancing(self):
        # A limit that advanced the DAG would be "no ACK, progress after N
        # minutes" -- the guarantee deleted.  It only makes the hold loud.
        receipt, adapters = self._intake()
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        _age_order(self.coord, order["id"], 3600)
        _report(self.coord, "execute", "alpha")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False, hold_limit_seconds=60)
        row = result["held"][0]
        self.assertTrue(row["over_limit"])
        self.assertEqual(row["limit_seconds"], 60)
        self.assertEqual(result["closed"], [])
        self.assertEqual(result["tasks"]["execute"]["state"], "running")
        self.assertEqual(result["tasks"]["review"]["state"], "pending")


if __name__ == "__main__":
    unittest.main()
