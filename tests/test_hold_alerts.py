"""A hold past its limit has to reach a person, exactly once.

v0.6.13 made an unacked order *loud* -- but only in the return value of one
``step()`` call.  Nothing carried that noise anywhere, so an unattended run
could sit on a dead order for a day and look healthy.  Each test below is one
guard on the delivery path: it must arrive, it must not repeat, it must not lie
about what it saw, and it must not be able to kill the heartbeat.
"""
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_evolution_protocol.coordination import commander, hold_alerts
from agent_evolution_protocol.federation import intake, supervisor


NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
SENT_AT = (NOW - timedelta(minutes=62)).isoformat()
EVENT = "EVT-AAAABBBBCCCC"
#: The id shape ``coord-delivery-status`` and ``coord-ack-message`` enforce.
MESSAGE_ID = re.compile(r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{8}$")


def _order(coord, *, task="execute", agent="alpha", sender="kernel",
           event_id=EVENT, created_at=SENT_AT, order_id="order-1",
           state="MESSAGE_VISIBLE"):
    """A durable order, in the shape ``commander.submit_order`` writes."""
    path = Path(coord) / "commander" / "inbox" / f"{order_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"id": order_id, "event_id": event_id, "target": agent, "sender": sender,
               "task": task, "state": state, "created_at": created_at,
               "attempts": [], "ack": None}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def _held(task="execute", **extra):
    """A ``held`` row exactly as ``supervisor._ack_blocks`` builds it."""
    row = {"task": task, "action": "hold_unacked", "verdict": "hold", "reason": "no_ack",
           "state": "sent_unacked", "event_id": EVENT, "sent_at": SENT_AT,
           "unacked_for_seconds": 3720, "limit_seconds": 900, "over_limit": True}
    row.update(extra)
    return row


def _mailbox(coord, who="kernel"):
    folder = Path(coord) / "messages" / who
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


class HoldAlertDeliveryTests(unittest.TestCase):
    def setUp(self):
        self._temp = TemporaryDirectory()
        self.coord = Path(self._temp.name) / "coordination"
        self.addCleanup(self._temp.cleanup)

    def test_crossing_the_limit_leaves_one_message_in_the_mailbox(self):
        # The gap this closes: `over_limit` used to exist only in a return value,
        # so an unattended run could hold a task forever and tell nobody.
        _order(self.coord)
        report = hold_alerts.emit(self.coord, [_held()], now=NOW)
        self.assertEqual(report["count"], 1)
        files = _mailbox(self.coord)
        self.assertEqual(len(files), 1)
        message = json.loads(files[0].read_text(encoding="utf-8"))
        # Same envelope as `coord-send`, so `coord-inbox`/`coord-digest` read it.
        self.assertEqual(message["kind"], "warning")
        self.assertEqual(message["to"], "kernel")
        self.assertEqual(message["topic"], "execute")
        self.assertTrue(MESSAGE_ID.fullmatch(message["id"]), message["id"])
        self.assertEqual(message["id"], files[0].stem)
        # "It is the existing channel" is measured, not claimed: the reader that
        # `coord-inbox`/`coord-digest` already use finds it with no changes.
        from agent_evolution_protocol import cli
        unread = cli.unread_messages(self.coord, "kernel")
        self.assertEqual([item["id"] for item in unread], [message["id"]])

    def test_the_same_hold_does_not_alert_twice(self):
        # An alert on every tick is a log flood wearing the costume of diligence.
        _order(self.coord)
        first = hold_alerts.emit(self.coord, [_held()], now=NOW)
        second = hold_alerts.emit(self.coord, [_held()], now=NOW + timedelta(seconds=30))
        self.assertEqual(first["count"], 1)
        self.assertEqual(second["count"], 0)
        self.assertEqual(second["suppressed"][0]["reason"], "already_alerted")
        self.assertEqual(len(_mailbox(self.coord)), 1)

    def test_a_hold_that_clears_and_returns_alerts_again(self):
        # Clearing needs an ACK or a signature, so a returning hold is always a
        # new send with a new sent_at -- and that is a new condition to report.
        _order(self.coord)
        self.assertEqual(hold_alerts.emit(self.coord, [_held()], now=NOW)["count"], 1)
        self.assertEqual(hold_alerts.emit(self.coord, [], now=NOW)["count"], 0)
        resent = (NOW + timedelta(minutes=5)).isoformat()
        _order(self.coord, order_id="order-2", created_at=resent)
        report = hold_alerts.emit(
            self.coord, [_held(sent_at=resent, unacked_for_seconds=5400)], now=NOW)
        self.assertEqual(report["count"], 1)
        self.assertEqual(len(_mailbox(self.coord)), 2)

    def test_a_hold_under_the_limit_is_not_an_alert(self):
        # A limit that makes noise before it is crossed is noise, not a warning.
        _order(self.coord)
        report = hold_alerts.emit(
            self.coord, [_held(unacked_for_seconds=120, over_limit=False)], now=NOW)
        self.assertEqual(report["count"], 0)
        self.assertEqual(report["waiting"][0]["task"], "execute")
        self.assertEqual(_mailbox(self.coord), [])

    def test_the_alert_names_the_task_the_agent_the_order_and_the_wait(self):
        # "something is stuck" is not actionable; a person needs who, what, how long.
        _order(self.coord, agent="beta")
        hold_alerts.emit(self.coord, [_held()], now=NOW)
        message = json.loads(_mailbox(self.coord)[0].read_text(encoding="utf-8"))
        body = message["text"]
        self.assertIn("execute", body)
        self.assertIn("beta", body)
        self.assertIn("order-1", body)
        self.assertIn(f"AEP-ACK:{EVENT}", body)
        self.assertIn("3720", body)
        self.assertIn("900", body)
        self.assertIn("--abandon", body)
        # The agent is not in the held row at all -- it comes from the order.
        self.assertEqual(message["hold"]["agent"], "beta")

    def test_an_unknown_order_still_alerts_and_says_what_is_missing(self):
        # Silence is the failure being fixed.  A hold with no order record is
        # still a stuck task, so it is reported with the gap named, not dropped.
        report = hold_alerts.emit(self.coord, [_held()], now=NOW)
        self.assertEqual(report["count"], 1)
        message = json.loads(_mailbox(self.coord)[0].read_text(encoding="utf-8"))
        self.assertEqual(message["hold"]["agent"], "unknown")
        self.assertFalse(message["hold"]["order_found"])
        self.assertIn("order record not found", message["text"])
        self.assertIn("execute", message["text"])

    def test_alerting_does_not_advance_the_order_or_the_task(self):
        # An alert is a message, not a state transition: the hold must still hold.
        order = _order(self.coord)
        path = Path(self.coord) / "commander" / "inbox" / "order-1.json"
        before = path.read_bytes()
        hold_alerts.emit(self.coord, [_held()], now=NOW)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(json.loads(before)["state"], order["state"])
        self.assertFalse((Path(self.coord) / "task_states").exists())

    def test_a_malformed_row_cannot_kill_the_heartbeat(self):
        # An alerting path that raises is worse than no alert: it takes the loop
        # down with it, and the loop is the thing keeping every agent moving.
        _order(self.coord)
        rows = ["not-a-row", {}, {"task": ""}, {"task": "execute", "over_limit": True,
                                                "sent_at": "the day before yesterday"},
                {"task": "execute", "over_limit": "yes", "sent_at": SENT_AT},
                None, _held()]
        report = hold_alerts.emit(self.coord, rows, now=NOW)
        self.assertEqual(report["count"], 1)
        self.assertEqual(report["alerts"][0]["task"], "execute")
        self.assertGreaterEqual(len(report["skipped"]), 3)
        self.assertTrue(all(row["reason"] for row in report["skipped"]))


class HoldAlertFromTheSupervisorTests(unittest.TestCase):
    """The real held row, not a hand-built one: the shapes have to agree."""

    def setUp(self):
        self._temp = TemporaryDirectory()
        self.root = Path(self._temp.name) / "state"
        self.coord = str(Path(self._temp.name) / "coordination")
        self.addCleanup(self._temp.cleanup)

    def _held_by_the_supervisor(self, limit=60):
        receipt = intake.intake(self.root, self.coord, text="跑通 README",
                                agents=["alpha", "beta"], shape="reviewed", sender="kernel")
        adapters = supervisor.build_adapters(coord_root=self.coord, agents=["alpha", "beta"],
                                             workspace_id=receipt["workspace"])
        order = commander._orders(self.coord)[0]
        path = Path(self.coord) / "commander" / "inbox" / f"{order['id']}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["state"] = "MESSAGE_VISIBLE"
        payload["session_id"] = "4242"
        # Aged against the real clock: `over_limit` is the supervisor's judgement
        # and this module must take it as given, not re-decide it from its own now.
        payload["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        folder = Path(self.coord) / "task_states"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "execute.json").write_text(json.dumps(
            {"task": "execute", "owner": "alpha", "phase": "done", "evidence": []}),
            encoding="utf-8")
        result = supervisor.step(self.root, self.coord, receipt["workspace"], adapters,
                                 dispatch=False, hold_limit_seconds=limit)
        return result

    def test_the_row_the_supervisor_reports_is_the_row_that_gets_delivered(self):
        result = self._held_by_the_supervisor()
        self.assertEqual(result["held"][0]["action"], "hold_unacked")
        self.assertTrue(result["held"][0]["over_limit"])
        # `step` now delivers the alert itself, so the message is already there;
        # a second emit from the same rows must be the one that stays quiet.
        self.assertEqual(result["alerts"]["count"], 1)
        report = hold_alerts.emit(self.coord, result["held"])
        self.assertEqual(report["count"], 0)
        self.assertEqual([row["reason"] for row in report["suppressed"]], ["already_alerted"])
        message = json.loads(_mailbox(self.coord)[0].read_text(encoding="utf-8"))
        self.assertEqual(message["hold"]["task"], "execute")
        self.assertEqual(message["hold"]["agent"], "alpha")
        self.assertEqual(message["hold"]["event_id"], result["held"][0]["event_id"])
        # And the DAG did not move: the alert reported a hold, it did not end one.
        self.assertEqual(result["tasks"]["execute"]["state"], "running")
        self.assertEqual(result["tasks"]["review"]["state"], "pending")


if __name__ == "__main__":
    unittest.main()
