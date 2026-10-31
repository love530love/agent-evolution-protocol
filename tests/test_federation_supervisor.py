"""The heartbeat: one input, several agents, nobody relaying messages by hand.

These tests drive the loop with durable records only.  No window is opened and
no client is launched -- what is under test is whether the supervisor notices
that work finished, died, or cannot safely start.
"""
import json
import unittest
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


def _set_order(coord, order_id, state, note=""):
    path = Path(coord) / "commander" / "inbox" / f"{order_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"] = state
    if note:
        payload["note"] = note
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


if __name__ == "__main__":
    unittest.main()
