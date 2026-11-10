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

from agent_evolution_protocol.coordination import claim_conflicts, commander, hold_alerts
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
        # The receipt gained a `result` field: what the readback could prove
        # about the agent's output, which is None here because this fixture
        # names no artifact.  Matching on the subset keeps the assertion about
        # the completion itself and leaves the verification separate.
        done = [row for row in result["closed"] if row["action"] == "complete"]
        self.assertEqual(len(done), 1)
        self.assertEqual({key: done[0][key] for key in ("task", "artifact")},
                         {"task": "execute", "artifact": ""})
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
    """No ACK, no downstream progress -- for orders that were actually sent.

    Every step here passes ``ack_policy="enforce"`` explicitly.  The default
    became ``"record"`` in v0.6.15, so a bare ``step()`` no longer holds -- and
    leaving these calls bare would have made them pass for the wrong reason
    once the default flipped, or fail while the property they test still held.
    Naming the policy is what keeps each assertion about the gate rather than
    about the default.
    """

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
                                 dispatch=False, ack_policy="enforce")
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
                               dispatch=False, ack_policy="enforce")
        self.assertEqual(held["closed"], [])
        transcript_bus.publish(self.coord, agent="alpha",
                               text="AEP-ACK:" + order["event_id"],
                               session_id="4242",
                               role="assistant", direction="incoming")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False, ack_policy="enforce")
        # The receipt gained a `result` field: what the readback could prove
        # about the agent's output, which is None here because this fixture
        # names no artifact.  Matching on the subset keeps the assertion about
        # the completion itself and leaves the verification separate.
        done = [row for row in result["closed"] if row["action"] == "complete"]
        self.assertEqual(len(done), 1)
        self.assertEqual({key: done[0][key] for key in ("task", "artifact")},
                         {"task": "execute", "artifact": ""})
        self.assertEqual(result["held"], [])

    def test_an_order_that_was_never_sent_is_not_held_for_an_ack(self):
        # Nothing reached a conversation, so nothing can acknowledge it; holding
        # here would be waiting for a reply to a message nobody delivered.
        receipt, adapters = self._intake()
        _report(self.coord, "execute", "alpha")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False, ack_policy="enforce")
        self.assertEqual(result["held"], [])
        self.assertEqual(result["tasks"]["execute"]["state"], "done")

    def test_an_abandoned_order_blocks_the_task_and_downstream_stays_pending(self):
        receipt, adapters = self._intake()
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        _report(self.coord, "execute", "alpha")
        first = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                dispatch=False, ack_policy="enforce")
        self.assertEqual([row["action"] for row in first["held"]], ["hold_unacked"])

        commander.abandon(self.coord, order["id"], actor="love",
                          reason="the client is wedged; re-planning tomorrow")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False, ack_policy="enforce")
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
        # hold_limit_seconds=None is the pre-v0.6.14 behaviour and must still be
        # available: a deployment may prefer to watch `held` itself.
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False, ack_policy="enforce", hold_limit_seconds=None)
        row = result["held"][0]
        self.assertEqual(row["verdict"], "hold")
        self.assertGreaterEqual(row["unacked_for_seconds"], 3600)
        self.assertIsNone(row["limit_seconds"])
        self.assertFalse(row["over_limit"])
        self.assertEqual(result["alerts"]["count"], 0)

    def test_crossing_the_hold_limit_reports_it_without_advancing(self):
        # A limit that advanced the DAG would be "no ACK, progress after N
        # minutes" -- the guarantee deleted.  It only makes the hold loud.
        receipt, adapters = self._intake()
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        _age_order(self.coord, order["id"], 3600)
        _report(self.coord, "execute", "alpha")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False, ack_policy="enforce", hold_limit_seconds=60)
        row = result["held"][0]
        self.assertTrue(row["over_limit"])
        self.assertEqual(row["limit_seconds"], 60)
        self.assertEqual(result["closed"], [])
        self.assertEqual(result["tasks"]["execute"]["state"], "running")
        self.assertEqual(result["tasks"]["review"]["state"], "pending")

    def test_a_hold_past_the_limit_leaves_an_alert_a_person_will_see(self):
        # Before this, `held` existed only in step()'s return value, so an
        # unattended loop held a task past its limit and told nobody.
        receipt, adapters = self._intake()
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        _age_order(self.coord, order["id"], 3600)
        _report(self.coord, "execute", "alpha")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False, ack_policy="enforce", hold_limit_seconds=60)
        self.assertEqual(result["alerts"]["count"], 1)
        alert = result["alerts"]["alerts"][0]
        self.assertEqual(alert["task"], "execute")
        self.assertEqual(alert["order"], order["id"])
        # The alert names the agent the order actually went to, taken from the
        # order record rather than guessed from the workspace task id.
        self.assertEqual(alert["agent"], "alpha")
        files = list((Path(self.coord) / "messages").rglob("*.json"))
        self.assertEqual(len(files), 1)
        body = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(body["kind"], "warning")
        self.assertIn("execute", body["text"])
        # An alert is a message, not a transition: the hold still holds.
        self.assertEqual(result["tasks"]["execute"]["state"], "running")

    def test_the_same_hold_is_announced_once(self):
        # Announced-once is asserted at the module level (``test_hold_alerts``),
        # because at *this* level a second identical step does not exist: the
        # engine hands the task off between steps, which mints a second order
        # that was never sent, so the next verdict is ``not sent`` and the hold
        # legitimately disappears.  What is asserted here is the part this layer
        # owns -- that one crossing produces exactly one message.
        receipt, adapters = self._intake()
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        _age_order(self.coord, order["id"], 3600)
        _report(self.coord, "execute", "alpha")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False, ack_policy="enforce", hold_limit_seconds=60)
        messages = list((Path(self.coord) / "messages").rglob("*.json"))
        self.assertEqual(len(messages), 1)
        again = hold_alerts.emit(self.coord, result["held"])
        self.assertEqual(again["count"], 0)
        self.assertEqual([row["reason"] for row in again["suppressed"]], ["already_alerted"])
        self.assertEqual(len(list((Path(self.coord) / "messages").rglob("*.json"))), 1)

    def test_a_hold_under_the_limit_says_nothing(self):
        receipt, adapters = self._intake()
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        _report(self.coord, "execute", "alpha")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False, ack_policy="enforce", hold_limit_seconds=60)
        self.assertEqual(result["alerts"]["count"], 0)
        self.assertEqual([row["task"] for row in result["alerts"]["waiting"]], ["execute"])
        self.assertEqual(list((Path(self.coord) / "messages").rglob("*.json")), [])


class RecordPolicyTests(unittest.TestCase):
    """An unacknowledged order no longer stops the DAG -- but it is still recorded.

    v0.6.15 flipped the default from ``enforce`` to ``record``.  The gate had no
    exception, so an adapter that cannot report an ACK, a session a person had
    closed, and a client that never renders the reply all produced the same
    outcome: the task pinned in ``running`` with the work possibly finished.  What
    the flip must *not* do is delete the fact, so almost every test here is about
    the receipt rather than about the advance.  ``ack.verdict == "hold"`` on a
    completed task is the whole design: it advanced, and it says so.
    """

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

    def _sent_unacked(self):
        """A sent order, an agent that reported done, and no acknowledgement."""
        receipt, adapters = self._intake()
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        _report(self.coord, "execute", "alpha")
        return receipt, adapters, order

    def _done(self, result):
        done = [row for row in result["closed"] if row["action"] == "complete"]
        self.assertEqual(len(done), 1, f"expected exactly one completion, got {result['closed']}")
        return done[0]

    def test_the_default_lets_an_unacknowledged_task_finish(self):
        receipt, adapters, _ = self._sent_unacked()
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertEqual(result["ack_policy"], "record")
        self.assertEqual(result["held"], [])
        self.assertEqual(result["tasks"]["execute"]["state"], "done")
        # The dependent node starts, which is the thing the old gate forbade.
        self.assertNotEqual(result["tasks"]["review"]["state"], "pending")

    def test_the_completion_carries_the_fact_that_nothing_acknowledged_it(self):
        receipt, adapters, _ = self._sent_unacked()
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        ack = self._done(result)["ack"]
        self.assertEqual(ack["verdict"], "hold")
        self.assertEqual(ack["reason"], "no_ack")
        self.assertEqual(ack["state"], "sent_unacked")
        # How long it waited is evidence; a bare "hold" is not.
        self.assertIsNotNone(ack["unacked_for_seconds"])
        self.assertTrue(ack["sent_at"])

    def test_the_released_bucket_names_the_task_that_advanced_unacked(self):
        # `closed` alone cannot express this: a released completion and a clean
        # one look identical there apart from the verdict.  The separate bucket
        # is what lets a watcher see them without parsing every receipt.
        receipt, adapters, _ = self._sent_unacked()
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertEqual([row["task"] for row in result["released"]], ["execute"])
        self.assertEqual(result["released"][0]["action"], "released_unacked")
        self.assertEqual(result["released"][0]["verdict"], "hold")

    def test_no_alert_is_raised_because_nothing_is_stuck(self):
        # The documented cost of the relaxation: `hold_alerts` fires on a hold,
        # and under `record` there is no hold left.  Asserting the silence keeps
        # the loss visible instead of letting it be discovered later.
        receipt, adapters, order = self._sent_unacked()
        _age_order(self.coord, order["id"], 7200)
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False, hold_limit_seconds=60)
        self.assertEqual(result["alerts"]["count"], 0)
        self.assertEqual(result["released"][0]["over_limit"], True)

    def test_a_human_abandonment_still_blocks_under_the_record_policy(self):
        # `ack_policy` governs a missing acknowledgement.  An abandonment is a
        # signature, which is the opposite kind of evidence, so relaxing one must
        # never relax the other -- otherwise "we gave up" would become "it's done".
        receipt, adapters, order = self._sent_unacked()
        commander.abandon(self.coord, order["id"], actor="love",
                          reason="the client is wedged; re-planning tomorrow")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertEqual([row["action"] for row in result["closed"]], ["abandoned"])
        self.assertEqual(result["tasks"]["execute"]["state"], "blocked")
        self.assertEqual(result["released"], [])

    def test_a_proven_contradiction_still_blocks_under_the_record_policy(self):
        # Same distinction one level down: a missing ACK is absence of evidence
        # about delivery, a contradicted artifact is evidence against the work.
        from agent_evolution_protocol.coordination import transcript_bus
        receipt, adapters = self._intake()
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        transcript_bus.publish(self.coord, agent="alpha",
                               text="AEP-ACK:" + order["event_id"] + "\nwrote docs/other.md",
                               session_id="4242", role="assistant", direction="incoming")
        _report(self.coord, "execute", "alpha", evidence=["docs/claimed.md"])
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertEqual([row["action"] for row in result["held"]], ["hold_result_mismatch"])
        self.assertEqual(result["tasks"]["execute"]["state"], "running")

    def test_the_receipt_tells_acked_from_never_sent(self):
        # Before v0.6.15 `_ack_blocks` returned None for both, so a receipt could
        # not distinguish "the agent replied" from "nobody could have replied".
        receipt, adapters = self._intake()
        _report(self.coord, "execute", "alpha")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertEqual(self._done(result)["ack"]["reason"], "not_sent")

    def test_an_acknowledged_order_says_acked_not_merely_clear(self):
        from agent_evolution_protocol.coordination import transcript_bus
        receipt, adapters = self._intake()
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        transcript_bus.publish(self.coord, agent="alpha",
                               text="AEP-ACK:" + order["event_id"],
                               session_id="4242", role="assistant", direction="incoming")
        _report(self.coord, "execute", "alpha")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertEqual(self._done(result)["ack"]["reason"], "acked")
        self.assertEqual(result["released"], [])

    def test_a_released_row_is_never_written_for_a_task_that_did_not_advance(self):
        # Both conditions at once: no acknowledgement *and* a contradicted
        # artifact.  The contradiction wins, so the task stays running -- and
        # `released` must not carry a row claiming it advanced.  This was a real
        # defect: the released row used to be appended before the contradiction
        # gate, so the receipt said "advanced" about a task that did not move.
        from agent_evolution_protocol.coordination import transcript_bus
        receipt, adapters = self._intake()
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        # No AEP-ACK token, so the verdict is `hold`; the transcript still names
        # a different artifact than the one the agent claims.
        transcript_bus.publish(self.coord, agent="alpha", text="wrote docs/other.md",
                               session_id="4242", role="assistant", direction="incoming")
        _report(self.coord, "execute", "alpha", evidence=["docs/claimed.md"])
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        self.assertEqual([row["action"] for row in result["held"]], ["hold_result_mismatch"])
        self.assertEqual(result["released"], [])
        self.assertEqual(result["tasks"]["execute"]["state"], "running")

    def test_an_unknown_policy_is_refused_rather_than_guessed(self):
        # A typo must not silently mean "enforce" or silently mean "record".
        receipt, adapters = self._intake()
        with self.assertRaises(ValueError):
            supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                            dispatch=False, ack_policy="maybe")
        with self.assertRaises(ValueError):
            supervisor.serve(self.root, self.coord, receipt["workspace"], adapters,
                             interval=1.0, max_ticks=1, dispatch=False,
                             ack_policy="maybe")


class ResultReadbackTests(unittest.TestCase):
    """Acknowledged is not the same as produced.

    The ACK gate proves the agent answered.  This proves -- or refuses to claim --
    that what it says it made is what the conversation shows it made.
    """

    def setUp(self):
        self._temp = TemporaryDirectory()
        self.root = Path(self._temp.name) / "state"
        self.coord = str(Path(self._temp.name) / "coordination")
        self.addCleanup(self._temp.cleanup)

    def _acked(self, *, claimed, said):
        """A task whose order was sent and acknowledged, with both statements."""
        from agent_evolution_protocol.coordination import transcript_bus
        receipt = intake.intake(self.root, self.coord, text="跑通 README",
                                agents=["alpha", "beta"], shape="reviewed", sender="kernel")
        adapters = supervisor.build_adapters(coord_root=self.coord, agents=["alpha", "beta"],
                                             workspace_id=receipt["workspace"])
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        transcript_bus.publish(self.coord, agent="alpha",
                               text="AEP-ACK:" + order["event_id"] + "\n" + said,
                               session_id="4242", role="assistant", direction="incoming")
        _report(self.coord, "execute", "alpha", evidence=[claimed])
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        return result, adapters

    def test_a_contradicted_artifact_does_not_complete_the_task(self):
        result, _ = self._acked(claimed="docs/claimed.md", said="wrote docs/other.md")
        self.assertEqual([row["action"] for row in result["held"]], ["hold_result_mismatch"])
        self.assertEqual(result["tasks"]["execute"]["state"], "running")
        self.assertEqual(result["closed"], [])

    def test_a_confirmed_artifact_completes_and_says_it_was_verified(self):
        result, _ = self._acked(claimed="docs/claimed.md", said="wrote docs/claimed.md")
        done = [row for row in result["closed"] if row["action"] == "complete"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["result"]["match"], True)

    def test_an_unverifiable_claim_still_completes_but_records_the_doubt(self):
        # None must not block: a module that refused whenever an agent named no
        # file would deadlock the DAG on its first task.  It must not read as
        # verified either, so the receipt carries the fact.
        result, _ = self._acked(claimed="done", said="all finished, nothing to show")
        done = [row for row in result["closed"] if row["action"] == "complete"]
        self.assertEqual(len(done), 1)
        self.assertIsNone(done[0]["result"]["match"])
        self.assertTrue(done[0]["result"]["reason"])

    def test_the_completion_says_the_comparison_was_anchored_to_the_conversation(self):
        # v0.6.14 shipped this guard unwired and said so only in a docstring.  If
        # the receipt does not carry it, "wired" and "forgotten" look identical
        # from outside -- which is how the first version survived review.
        result, _ = self._acked(claimed="docs/claimed.md", said="wrote docs/claimed.md")
        done = [row for row in result["closed"] if row["action"] == "complete"][0]
        self.assertTrue(done["result"]["session_checked"])
        self.assertFalse(done["result"]["session_unknown"])

    def test_a_path_from_another_conversation_no_longer_counts_as_this_result(self):
        # The point of wiring the guard: same agent, same path, different window.
        # Before v0.6.15 this compared against every line the agent ever wrote and
        # called it a match; now the anchor rejects the foreign line and the
        # verdict is "nothing comparable", not "verified".
        from agent_evolution_protocol.coordination import transcript_bus
        receipt = intake.intake(self.root, self.coord, text="跑通 README",
                                agents=["alpha", "beta"], shape="reviewed", sender="kernel")
        adapters = supervisor.build_adapters(coord_root=self.coord, agents=["alpha", "beta"],
                                             workspace_id=receipt["workspace"])
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        transcript_bus.publish(self.coord, agent="alpha",
                               text="AEP-ACK:" + order["event_id"],
                               session_id="4242", role="assistant", direction="incoming")
        # The claimed artifact is only ever named in a *different* conversation.
        transcript_bus.publish(self.coord, agent="alpha", text="wrote docs/claimed.md",
                               session_id="9999", role="assistant", direction="incoming")
        _report(self.coord, "execute", "alpha", evidence=["docs/claimed.md"])
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        done = [row for row in result["closed"] if row["action"] == "complete"][0]
        self.assertTrue(done["result"]["session_checked"])
        self.assertIsNone(done["result"]["match"])
        self.assertEqual(done["result"]["reason"], "foreign_session")

    def test_an_order_with_no_session_cannot_be_anchored_and_says_so(self):
        # The honest failure: no anchor means the whole transcript was compared,
        # and the receipt must not let that pass for an anchored verification.
        #
        # ``match`` stays True here on purpose.  It answers "does what the agent
        # said agree with what the transcript shows", and it does.  Whether the
        # comparison was anchored to the dispatched conversation is a *different*
        # question, answered by ``session_checked``.  Collapsing the two would
        # either throw away real agreement (6 of the 24 orders on disk carry no
        # ``session_id``) or overstate it; two fields keep both readings honest.
        from agent_evolution_protocol.coordination import transcript_bus
        receipt = intake.intake(self.root, self.coord, text="跑通 README",
                                agents=["alpha", "beta"], shape="reviewed", sender="kernel")
        adapters = supervisor.build_adapters(coord_root=self.coord, agents=["alpha", "beta"],
                                             workspace_id=receipt["workspace"])
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE")   # no session_id
        transcript_bus.publish(self.coord, agent="alpha",
                               text="AEP-ACK:" + order["event_id"] + "\nwrote docs/claimed.md",
                               session_id="4242", role="assistant", direction="incoming")
        _report(self.coord, "execute", "alpha", evidence=["docs/claimed.md"])
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False)
        done = [row for row in result["closed"] if row["action"] == "complete"][0]
        self.assertFalse(done["result"]["session_checked"])
        self.assertTrue(done["result"]["match"])


class UnattendedStrictCompletionTests(unittest.TestCase):
    """A hands-off campaign may only advance on a fully evidenced hand-off.

    This is deliberately stricter than ``ack_policy="enforce"``: enforce
    protects the delivery acknowledgement, while unattended-strict also
    requires the result claim to be positively observed in that same session.
    """

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

    def _prepared_order(self):
        receipt, adapters = self._intake()
        order = commander._orders(self.coord)[0]
        _set_order(self.coord, order["id"], "MESSAGE_VISIBLE", session_id="4242")
        return receipt, adapters, order

    def test_missing_target_ack_holds_even_when_record_policy_would_normally_advance(self):
        from agent_evolution_protocol.coordination import transcript_bus
        receipt, adapters, _ = self._prepared_order()
        transcript_bus.publish(self.coord, agent="alpha", text="wrote docs/claimed.md",
                               session_id="4242", role="assistant", direction="incoming")
        _report(self.coord, "execute", "alpha", evidence=["docs/claimed.md"])

        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False, ack_policy="record",
                                 completion_policy="unattended-strict")

        self.assertEqual(result["closed"], [])
        self.assertEqual(result["tasks"]["execute"]["state"], "running")
        held = result["held"][0]
        self.assertEqual(held["action"], "hold_unattended_strict")
        self.assertEqual(held["reasons"], ["target_ack_missing"])
        self.assertTrue(held["result"]["session_checked"])
        self.assertIs(held["result"]["match"], True)

    def test_missing_or_unverifiable_result_holds_after_a_real_ack(self):
        from agent_evolution_protocol.coordination import transcript_bus
        receipt, adapters, order = self._prepared_order()
        transcript_bus.publish(self.coord, agent="alpha",
                               text="AEP-ACK:" + order["event_id"],
                               session_id="4242", role="assistant", direction="incoming")
        _report(self.coord, "execute", "alpha", evidence=["done"])

        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False, completion_policy="unattended-strict")

        self.assertEqual(result["closed"], [])
        held = result["held"][0]
        self.assertEqual(held["action"], "hold_unattended_strict")
        self.assertEqual(held["reasons"], ["result_not_verified"])
        self.assertEqual(held["ack"]["reason"], "acked")
        self.assertIsNone(held["result"]["match"])

    def test_ambiguous_session_result_cannot_complete_after_a_real_ack(self):
        from agent_evolution_protocol.coordination import transcript_bus
        receipt, adapters, order = self._prepared_order()
        transcript_bus.publish(self.coord, agent="alpha",
                               text="AEP-ACK:" + order["event_id"],
                               session_id="4242", role="assistant", direction="incoming")
        # The output text cannot be attributed to the original conversation.
        transcript_bus.publish(self.coord, agent="alpha", text="wrote docs/claimed.md",
                               role="assistant", direction="incoming")
        _report(self.coord, "execute", "alpha", evidence=["docs/claimed.md"])

        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False, completion_policy="unattended-strict")

        self.assertEqual(result["closed"], [])
        held = result["held"][0]
        self.assertEqual(held["action"], "hold_unattended_strict")
        self.assertIn("result_not_verified", held["reasons"])
        self.assertIn("result_session_ambiguous", held["reasons"])
        self.assertEqual(held["ack"]["reason"], "acked")

    def test_acknowledged_positive_session_bound_result_completes(self):
        from agent_evolution_protocol.coordination import transcript_bus
        receipt, adapters, order = self._prepared_order()
        transcript_bus.publish(self.coord, agent="alpha",
                               text="AEP-ACK:" + order["event_id"] + "\nwrote docs/claimed.md",
                               session_id="4242", role="assistant", direction="incoming")
        _report(self.coord, "execute", "alpha", evidence=["docs/claimed.md"])

        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False, completion_policy="unattended-strict")

        done = [row for row in result["closed"] if row["action"] == "complete"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["ack"]["reason"], "acked")
        self.assertIs(done[0]["result"]["match"], True)
        self.assertTrue(done[0]["result"]["session_checked"])
        self.assertEqual(result["completion_policy"], "unattended-strict")

    def test_unknown_completion_policy_is_refused(self):
        receipt, adapters = self._intake()
        with self.assertRaises(ValueError):
            supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                            dispatch=False, completion_policy="optimistic")
        with self.assertRaises(ValueError):
            supervisor.serve(self.root, self.coord, receipt["workspace"], adapters,
                             interval=1.0, max_ticks=1, dispatch=False,
                             completion_policy="optimistic")


if __name__ == "__main__":
    unittest.main()
