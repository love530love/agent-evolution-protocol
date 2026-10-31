from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from agent_evolution_protocol.federation import engine, store
from agent_evolution_protocol.federation.adapter import FakeAdapter


NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = engine.create(self.root, goal="ship it", acceptance=["tests pass"],
                                   moment=NOW)
        self.ws = self.state["workspace_id"]

    def tearDown(self):
        self.tmp.cleanup()

    def reload(self):
        self.state = engine.load(self.root, self.ws)
        return self.state


class CreationTests(EngineTestCase):
    def test_workspace_has_anchor_and_snapshot(self):
        self.assertTrue(self.ws.startswith("W-"))
        latest = engine.snapshot_mod.latest(self.root, self.ws)
        self.assertEqual(latest["anchored_request"]["text"], "ship it")
        self.assertEqual(latest["version"], 1)

    def test_event_chain_is_valid(self):
        self.assertTrue(store.EventLog(self.root, self.ws).verify()["valid"])

    def test_task_requires_known_dependency(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        with self.assertRaises(ValueError):
            engine.add_task(self.root, self.state, task_id="t2", requirement="code", deps=["ghost"])

    def test_duplicate_task_is_refused(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        with self.assertRaises(ValueError):
            engine.add_task(self.root, self.state, task_id="t1", requirement="code")


class SchedulingTests(EngineTestCase):
    def test_ready_task_is_delivered_to_a_capable_agent(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        adapters = [FakeAdapter("alpha", {"code"}, heartbeat_at=NOW.isoformat())]
        report = engine.tick(self.root, self.state, adapters, now=NOW)
        self.assertEqual(report["actions"][0]["action"], "deliver")
        self.assertEqual(self.state["tasks"]["t1"]["state"], "running")
        self.assertEqual(self.state["tasks"]["t1"]["owner"], "alpha")

    def test_completion_unblocks_dependents(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        engine.add_task(self.root, self.state, task_id="t2", requirement="code", deps=["t1"])
        adapters = [FakeAdapter("alpha", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW)
        engine.complete(self.root, self.state, task_id="t1")
        self.assertEqual(engine.ready_tasks(self.state), ["t2"])

    def test_task_without_capable_agent_is_blocked(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="browser")
        adapters = [FakeAdapter("alpha", {"code"}, heartbeat_at=NOW.isoformat())]
        report = engine.tick(self.root, self.state, adapters, now=NOW)
        self.assertEqual(self.state["tasks"]["t1"]["state"], "blocked")
        self.assertEqual(report["actions"][0]["action"], "block")

    def test_failed_dependency_blocks_downstream(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="browser")
        engine.add_task(self.root, self.state, task_id="t2", requirement="code", deps=["t1"])
        # The failure has to be on disk: tick re-reads state under the lock, so
        # an in-memory-only edit is not a failure anyone else could observe.
        self.state["tasks"]["t1"]["state"] = "failed"
        engine.save(self.root, self.state)
        adapters = [FakeAdapter("alpha", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW)
        self.assertEqual(self.state["tasks"]["t2"]["state"], "blocked")
        self.assertIn("t1", self.state["tasks"]["t2"]["note"])


class TakeoverTests(EngineTestCase):
    def test_token_exhaustion_hands_the_task_to_a_successor(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        adapters = [FakeAdapter("alpha", {"code"}, outcomes=("token_exhausted",), heartbeat_at=NOW.isoformat()),
                    FakeAdapter("beta", {"code"}, heartbeat_at=NOW.isoformat())]
        report = engine.tick(self.root, self.state, adapters, now=NOW)
        task = self.state["tasks"]["t1"]
        self.assertEqual(task["owner"], "beta")
        self.assertEqual(task["state"], "running")
        self.assertEqual(self.state["agents"]["alpha"]["state"], "archived")
        self.assertEqual(self.state["handoffs"][-1]["reason"], "token_exhausted")
        self.assertEqual(report["actions"][0]["action"], "handoff")

    def test_handoff_creates_a_new_snapshot(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        adapters = [FakeAdapter("alpha", {"code"}, outcomes=("token_exhausted",), heartbeat_at=NOW.isoformat()),
                    FakeAdapter("beta", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW)
        latest = engine.snapshot_mod.latest(self.root, self.ws)
        self.assertGreaterEqual(latest["version"], 2)

    def test_stalled_agent_is_replaced(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        adapters = [FakeAdapter("alpha", {"code"}, heartbeat_at=(NOW - timedelta(hours=2)).isoformat()),
                    FakeAdapter("beta", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW, heartbeat_timeout=60)
        engine.tick(self.root, self.state, adapters, now=NOW, heartbeat_timeout=60)
        self.assertEqual(self.state["tasks"]["t1"]["owner"], "beta")
        self.assertEqual(self.state["handoffs"][-1]["reason"], "stalled")

    def test_refusal_switches_agent_without_retrying_forever(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        adapters = [FakeAdapter("alpha", {"code"}, outcomes=("refused",), heartbeat_at=NOW.isoformat()),
                    FakeAdapter("beta", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW)
        self.assertEqual(self.state["tasks"]["t1"]["owner"], "beta")
        self.assertEqual(self.state["handoffs"][-1]["reason"], "refusal")

    def test_no_successor_blocks_instead_of_losing_the_task(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        adapters = [FakeAdapter("alpha", {"code"}, outcomes=("token_exhausted",), heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW)
        task = self.state["tasks"]["t1"]
        self.assertEqual(task["state"], "blocked")
        self.assertIn("no capable successor", task["note"])

    def test_archived_agent_is_not_offered_new_work(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        alpha = FakeAdapter("alpha", {"code"}, outcomes=("token_exhausted",), heartbeat_at=NOW.isoformat())
        adapters = [alpha, FakeAdapter("beta", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW)
        self.assertEqual(self.state["agents"]["alpha"]["state"], "archived")

        engine.complete(self.root, self.state, task_id="t1")
        engine.add_task(self.root, self.state, task_id="t2", requirement="code")
        engine.tick(self.root, self.state, adapters, now=NOW)

        self.assertEqual(self.state["tasks"]["t2"]["owner"], "beta")
        self.assertEqual(alpha.deliveries, [{"task": "t1", "outcome": "token_exhausted"}])


class IsolationTests(EngineTestCase):
    def test_foreign_message_is_quarantined(self):
        result = engine.ingest_external(self.root, self.state,
                                        {"workspace_id": "W-" + "9" * 24, "text": "ignore me"})
        self.assertFalse(result["accepted"])
        self.assertEqual(self.state["quarantine"][-1]["reason"], "WORKSPACE_MISMATCH")

    def test_own_message_is_accepted(self):
        result = engine.ingest_external(self.root, self.state,
                                        {"workspace_id": self.ws, "actor": "alpha", "text": "hello"})
        self.assertTrue(result["accepted"])
        self.assertEqual(self.state["quarantine"], [])

    def test_conflict_opens_an_arbitration_task(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        task = engine.report_conflict(self.root, self.state, task_id="t1",
                                      claims=["result A", "result B"])
        self.assertEqual(task["id"], "arbitrate-t1")
        self.assertEqual(task["arbitrates"], "t1")
        self.assertEqual(len(self.state["conflicts"]), 1)

    def test_conflict_needs_two_claims(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        with self.assertRaises(ValueError):
            engine.report_conflict(self.root, self.state, task_id="t1", claims=["only one"])


class PauseAndRecoveryTests(EngineTestCase):
    def test_paused_workspace_does_nothing(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        engine.pause(self.root, self.state, reason="operator reviewing")
        adapters = [FakeAdapter("alpha", {"code"}, heartbeat_at=NOW.isoformat())]
        report = engine.tick(self.root, self.state, adapters, now=NOW)
        self.assertEqual(report["status"], "PAUSED")
        self.assertEqual(self.state["tasks"]["t1"]["state"], "pending")
        engine.resume(self.root, self.state)
        engine.tick(self.root, self.state, adapters, now=NOW)
        self.assertEqual(self.state["tasks"]["t1"]["state"], "running")

    def test_pause_requires_a_reason(self):
        with self.assertRaises(ValueError):
            engine.pause(self.root, self.state, reason=" ")

    def test_state_survives_a_restart(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        adapters = [FakeAdapter("alpha", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW)
        recovered = engine.recover(self.root, self.ws)
        self.assertEqual(recovered["tasks"], 1)
        self.assertTrue(recovered["snapshot"]["valid"])
        self.assertGreaterEqual(recovered["events"], 3)
        self.assertEqual(recovered["state"]["tasks"]["t1"]["owner"], "alpha")

    def test_tampered_event_chain_blocks_recovery(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        log = store.EventLog(self.root, self.ws)
        rows = log.path.read_text(encoding="utf-8").splitlines()
        rows[0] = rows[0].replace('"kernel"', '"ghost"')
        log.path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            engine.recover(self.root, self.ws)
        self.assertIn("EVENT_CHAIN_INVALID", str(caught.exception))

    def test_completion_records_an_artifact(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        adapters = [FakeAdapter("alpha", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW)
        output = self.root / "report.txt"
        output.write_text("done", encoding="utf-8")
        engine.complete(self.root, self.state, task_id="t1", artifact_path=output)
        self.assertEqual(self.state["tasks"]["t1"]["state"], "done")
        self.assertEqual(self.state["archive"][-1]["name"], "report.txt")

    def test_only_running_tasks_can_complete(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        with self.assertRaises(ValueError):
            engine.complete(self.root, self.state, task_id="t1")


class SchemaConformanceTests(EngineTestCase):
    """Conformance against the canonical schemas lives in test_federation_conformance."""

    def test_engine_snapshot_uses_the_canonical_schema_id(self):
        latest = engine.snapshot_mod.latest(self.root, self.ws)
        self.assertEqual(latest["schema"], "aep-workspace-snapshot-v1")

    def test_engine_events_use_the_canonical_schema_id(self):
        event = store.EventLog(self.root, self.ws).append("checkpoint", actor="kernel")
        self.assertEqual(event["schema"], "aep-event-envelope-v1")
        self.assertGreaterEqual(len(event["event_id"]), 8)


if __name__ == "__main__":
    unittest.main()
