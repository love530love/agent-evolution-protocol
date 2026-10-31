"""The strategy library must actually drive scheduling, and must stay bounded.

Two things are being tested here and they pull in opposite directions:

* learning has to close the loop — a recovery that worked becomes a proposal,
  then a promoted rule, then the default choice next time;
* learning must not become a way to bypass a safety decision. A promoted
  strategy may only choose among reversible actions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from agent_evolution_protocol.federation import engine, evolution, store
from agent_evolution_protocol.federation.adapter import FakeAdapter


NOW = datetime(2026, 10, 31, 3, 0, tzinfo=timezone.utc)


class LoopTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = engine.create(self.root, goal="ship it", acceptance=["tests pass"],
                                   moment=NOW)
        self.ws = self.state["workspace_id"]

    def tearDown(self):
        self.tmp.cleanup()

    def library(self):
        return self.state["strategies"]["strategies"]

    def events(self, kind):
        return [row for row in store.EventLog(self.root, self.ws).events()
                if row["kind"] == kind]

    def fail_once(self, index, signal="token_exhausted"):
        """Deliver one task to an agent that reports ``signal``."""
        task_id = f"t{index}"
        engine.add_task(self.root, self.state, task_id=task_id, requirement="code", moment=NOW)
        adapters = [FakeAdapter(f"a{index}", {"code"}, outcomes=(signal,),
                                heartbeat_at=NOW.isoformat()),
                    FakeAdapter(f"b{index}", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW, moment=NOW)
        engine.complete(self.root, self.state, task_id=task_id, moment=NOW)
        return task_id


class LearningLoopTests(LoopTestCase):
    def test_one_recovery_is_only_a_proposal(self):
        self.fail_once(1)
        rows = list(self.library().values())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "proposed")
        self.assertEqual(rows[0]["successes"], 1)
        self.assertEqual(rows[0]["tags"], ["token_exhausted"])
        self.assertEqual(rows[0]["params"], {"action": "handoff"})

    def test_two_recoveries_promote_the_strategy(self):
        self.fail_once(1)
        self.fail_once(2)
        rows = list(self.library().values())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "promoted")
        self.assertEqual(rows[0]["successes"], 2)
        self.assertEqual(len(self.events("strategy_promoted")), 1)

    def test_a_promoted_strategy_is_reused_on_the_next_failure(self):
        self.fail_once(1)
        self.fail_once(2)
        self.assertEqual(list(self.library().values())[0]["status"], "promoted")

        engine.add_task(self.root, self.state, task_id="t3", requirement="code", moment=NOW)
        adapters = [FakeAdapter("a3", {"code"}, outcomes=("token_exhausted",),
                                heartbeat_at=NOW.isoformat()),
                    FakeAdapter("b3", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW, moment=NOW)

        strategy_id = list(self.library())[0]
        self.assertEqual(self.state["tasks"]["t3"]["strategy"], strategy_id)
        self.assertEqual(self.state["tasks"]["t3"]["owner"], "b3")

    def test_a_failed_recovery_blocks_promotion(self):
        self.fail_once(1)
        # The same signal with no capable successor: the strategy gets a failure.
        engine.add_task(self.root, self.state, task_id="t9", requirement="code", moment=NOW)
        adapters = [FakeAdapter("a9", {"code"}, outcomes=("token_exhausted",),
                                heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW, moment=NOW)
        self.assertEqual(self.state["tasks"]["t9"]["state"], "blocked")

        row = list(self.library().values())[0]
        self.assertEqual(row["failures"], 1)
        self.assertEqual(row["status"], "proposed")


class OverrideTests(LoopTestCase):
    def plant_promoted(self, *, signal, action, successes=5):
        row = evolution.propose(self.state["strategies"], name=f"try-{action}",
                                tags=[signal], params={"action": action}, moment=NOW)
        row["successes"] = successes
        evolution.promote(self.state["strategies"], row["id"], moment=NOW)
        engine.save(self.root, self.state)
        return row

    def test_a_promoted_strategy_replaces_the_default_action(self):
        row = self.plant_promoted(signal="token_exhausted", action="switch_agent")
        engine.add_task(self.root, self.state, task_id="t1", requirement="code", moment=NOW)
        adapters = [FakeAdapter("alpha", {"code"}, outcomes=("token_exhausted",),
                                heartbeat_at=NOW.isoformat()),
                    FakeAdapter("beta", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW, moment=NOW)

        applied = self.events("strategy_applied")
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["payload"]["default"], "handoff")
        self.assertEqual(applied[0]["payload"]["chosen"], "switch_agent")
        self.assertEqual(self.state["tasks"]["t1"]["strategy"], row["id"])
        self.assertEqual(self.state["tasks"]["t1"]["owner"], "beta")

    def test_a_strategy_cannot_turn_an_escalation_into_a_retry(self):
        self.plant_promoted(signal="captcha", action="retry")
        engine.add_task(self.root, self.state, task_id="t1", requirement="code", moment=NOW)
        adapters = [FakeAdapter("alpha", {"code"}, outcomes=("captcha",),
                                heartbeat_at=NOW.isoformat()),
                    FakeAdapter("beta", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW, moment=NOW)

        self.assertEqual(self.state["tasks"]["t1"]["state"], "failed")
        self.assertEqual(self.events("strategy_applied"), [])
        self.assertEqual(self.state["tasks"]["t1"]["strategy"], "")

    def test_an_unpromoted_strategy_is_never_applied(self):
        row = evolution.propose(self.state["strategies"], name="try-switch",
                                tags=["token_exhausted"], params={"action": "switch_agent"},
                                moment=NOW)
        engine.save(self.root, self.state)
        engine.add_task(self.root, self.state, task_id="t1", requirement="code", moment=NOW)
        adapters = [FakeAdapter("alpha", {"code"}, outcomes=("token_exhausted",),
                                heartbeat_at=NOW.isoformat()),
                    FakeAdapter("beta", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW, moment=NOW)

        self.assertEqual(self.events("strategy_applied"), [])
        self.assertNotEqual(self.state["tasks"]["t1"]["strategy"], row["id"])


if __name__ == "__main__":
    unittest.main()
