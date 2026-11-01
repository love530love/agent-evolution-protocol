from datetime import datetime, timedelta, timezone
import json
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


class ReviveTests(EngineTestCase):
    """A sign-off is a decision, and a decision that cannot be undone is a trap.

    ``abandon`` turns a running task into ``blocked``, which is terminal for the
    DAG, and v0.6.14 shipped with no way back: an operator who gave up one
    minute too early had to edit ``state.json`` by hand.  ``revive`` is the way
    back, and it has to be as strict about *what* it undoes as ``abandon`` is
    about what it stops.
    """

    def _running(self, *, task_id="t1", deps=()):
        engine.add_task(self.root, self.state, task_id=task_id, requirement="code",
                        deps=deps)
        adapters = [FakeAdapter("alpha", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW)
        return adapters

    def test_reviving_restores_the_task_and_keeps_the_sign_off(self):
        self._running()
        engine.abandon(self.root, self.state, task_id="t1", reason="client wedged",
                       actor="love", moment=NOW)
        task = engine.revive(self.root, self.state, task_id="t1",
                             reason="the client recovered", actor="love", moment=NOW)
        self.assertEqual(task["state"], "running")
        self.assertFalse(task["already_revived"])
        self.assertEqual(task["owner"], "alpha")
        # The abandonment is not deleted; it is marked as undone.  A record that
        # reads "abandoned at T by X" is still true after T2.
        kept = engine.load(self.root, self.ws)["tasks"]["t1"]["abandonments"]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["by"], "love")
        self.assertIn("wedged", kept[0]["reason"])
        self.assertEqual(kept[0]["revived_by"], "love")
        self.assertIn("recovered", kept[0]["revive_reason"])
        self.assertTrue(kept[0]["revived_at"])

    def test_the_revival_is_in_the_event_log(self):
        self._running()
        engine.abandon(self.root, self.state, task_id="t1", reason="client wedged",
                       actor="love", moment=NOW)
        engine.revive(self.root, self.state, task_id="t1", reason="client recovered",
                      actor="love", moment=NOW)
        events = [row["kind"] for row in store.EventLog(self.root, self.ws).events()]
        self.assertIn("task_abandoned", events)
        self.assertIn("task_revived", events)

    def test_a_reason_and_an_actor_are_required(self):
        self._running()
        engine.abandon(self.root, self.state, task_id="t1", reason="wedged",
                       actor="love", moment=NOW)
        with self.assertRaises(ValueError):
            engine.revive(self.root, self.state, task_id="t1", reason="   ",
                          actor="love", moment=NOW)
        with self.assertRaises(ValueError):
            engine.revive(self.root, self.state, task_id="t1", reason="recovered",
                          actor="", moment=NOW)

    def test_only_a_blocked_task_can_be_revived(self):
        # A running task was never abandoned; reviving it would be a rewrite of
        # history rather than the reversal of one decision.
        self._running()
        with self.assertRaises(ValueError) as caught:
            engine.revive(self.root, self.state, task_id="t1", reason="why not",
                          actor="love", moment=NOW)
        self.assertIn("running", str(caught.exception))

    def test_a_task_blocked_for_another_reason_is_not_revived(self):
        # ``blocked`` is also where "no capable agent" lands.  Reviving it would
        # discard the reason it was blocked and hand it to nobody again.
        engine.add_task(self.root, self.state, task_id="t1", requirement="quantum")
        adapters = [FakeAdapter("alpha", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW)
        self.assertEqual(self.state["tasks"]["t1"]["state"], "blocked")
        with self.assertRaises(ValueError) as caught:
            engine.revive(self.root, self.state, task_id="t1", reason="try again",
                          actor="love", moment=NOW)
        self.assertIn("not an abandonment", str(caught.exception))
        self.assertEqual(engine.load(self.root, self.ws)["tasks"]["t1"]["state"], "blocked")

    def test_reviving_twice_keeps_the_first_signature(self):
        self._running()
        engine.abandon(self.root, self.state, task_id="t1", reason="wedged",
                       actor="love", moment=NOW)
        engine.revive(self.root, self.state, task_id="t1", reason="first decision",
                      actor="love", moment=NOW)
        again = engine.revive(self.root, self.state, task_id="t1",
                              reason="a different story", actor="someone-else", moment=NOW)
        self.assertTrue(again["already_revived"])
        kept = engine.load(self.root, self.ws)["tasks"]["t1"]["abandonments"][-1]
        self.assertEqual(kept["revive_reason"], "first decision")
        self.assertEqual(kept["revived_by"], "love")

    def test_a_second_abandonment_does_not_erase_the_first(self):
        self._running()
        engine.abandon(self.root, self.state, task_id="t1", reason="first",
                       actor="love", moment=NOW)
        engine.revive(self.root, self.state, task_id="t1", reason="resumed",
                      actor="love", moment=NOW)
        engine.abandon(self.root, self.state, task_id="t1", reason="second",
                       actor="love", moment=NOW)
        kept = engine.load(self.root, self.ws)["tasks"]["t1"]["abandonments"]
        self.assertEqual([row["reason"] for row in kept], ["first", "second"])
        self.assertNotIn("revived_at", kept[-1])

    def test_a_dependent_blocked_by_the_abandonment_is_queued_again(self):
        # Without this, reviving a dependency leaves its dependent blocked
        # forever with a note that says "blocked by ['t1']" while t1 is running
        # again -- a record stating something untrue.
        self._running()
        engine.add_task(self.root, self.state, task_id="t2", requirement="code", deps=["t1"])
        engine.abandon(self.root, self.state, task_id="t1", reason="wedged",
                       actor="love", moment=NOW)
        adapters = [FakeAdapter("alpha", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW)
        self.assertEqual(self.state["tasks"]["t2"]["state"], "blocked")
        task = engine.revive(self.root, self.state, task_id="t1", reason="recovered",
                             actor="love", moment=NOW)
        self.assertEqual(task["reopened"], ["t2"])
        reloaded = engine.load(self.root, self.ws)["tasks"]
        self.assertEqual(reloaded["t2"]["state"], "pending")
        self.assertNotIn("blocked_by_deps", reloaded["t2"])

    def test_a_chain_behind_the_revived_task_is_queued_but_not_started(self):
        self._running()
        engine.add_task(self.root, self.state, task_id="t2", requirement="code", deps=["t1"])
        engine.add_task(self.root, self.state, task_id="t3", requirement="code", deps=["t2"])
        engine.abandon(self.root, self.state, task_id="t1", reason="wedged",
                       actor="love", moment=NOW)
        adapters = [FakeAdapter("alpha", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW)
        self.assertEqual([self.state["tasks"][key]["state"] for key in ("t2", "t3")],
                         ["blocked", "blocked"])
        engine.revive(self.root, self.state, task_id="t1", reason="recovered",
                      actor="love", moment=NOW)
        reloaded = engine.load(self.root, self.ws)
        self.assertEqual([reloaded["tasks"][key]["state"] for key in ("t2", "t3")],
                         ["pending", "pending"])
        # Queued is not started: only t1 has no unfinished dependency.
        self.assertEqual(engine.ready_tasks(reloaded), [])


class ReviveCliTests(EngineTestCase):
    """``federate revive`` is the only way back that does not edit json by hand."""

    def _blocked(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code")
        adapters = [FakeAdapter("alpha", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW)
        engine.abandon(self.root, self.state, task_id="t1", reason="client wedged",
                       actor="love", moment=NOW)

    def _run(self, *extra):
        """Drive the real parser, so the flag names are part of what is tested."""
        from agent_evolution_protocol import cli

        argv = ["--coord-root", str(self.root / "coordination"), "federate", "revive",
                "--federation-root", str(self.root), "--workspace", self.ws,
                "--task", "t1", "--by", "love", "--reason", "the client recovered", *extra]
        args = cli.build_parser().parse_args(argv)
        args.func(args)

    def test_the_command_reports_the_task_it_resumed(self):
        from contextlib import redirect_stdout
        from io import StringIO

        self._blocked()
        with redirect_stdout(StringIO()) as output:
            self._run()
        value = json.loads(output.getvalue())
        self.assertEqual(value["schema"], "aep-federate-revive-v1")
        self.assertEqual(value["task"]["state"], "running")
        self.assertFalse(value["task"]["already_revived"])

    def test_an_unsigned_revival_is_refused_with_a_message(self):
        from agent_evolution_protocol import cli

        self._blocked()
        with self.assertRaises(cli.ProtocolError):
            self._run("--reason", "")


if __name__ == "__main__":
    unittest.main()
