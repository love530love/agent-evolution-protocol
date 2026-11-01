"""Ending a wait is a decision with a name on it, not a clock running out.

"No ACK, no progress" has no ceiling by design.  That is only defensible if there
is a way to stop waiting that cannot be mistaken for the work having happened, so
``commander.abandon`` requires a person and a written reason and records both.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_evolution_protocol.coordination import commander


def _sent(coord, *, state="MESSAGE_VISIBLE"):
    """One order, already in the conversation, with no reply."""
    order = commander.submit_order(Path(coord), target="qoder", task="AEP-ABANDON-1",
                                   text="do the thing", sender="kernel")
    path = Path(coord) / "commander" / "inbox" / f"{order['id']}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"] = state
    payload["session_id"] = "4242"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


class AbandonTests(unittest.TestCase):
    def setUp(self):
        self._temp = TemporaryDirectory()
        self.coord = str(Path(self._temp.name) / "coordination")
        self.addCleanup(self._temp.cleanup)

    def test_a_named_person_ends_the_wait_and_the_reason_is_kept(self):
        order = _sent(self.coord)
        value = commander.abandon(self.coord, order["id"], actor="love",
                                  reason="the client is wedged; re-planning tomorrow")
        self.assertEqual(value["state"], "abandoned")
        self.assertFalse(value["accepted"])
        self.assertFalse(value["already_abandoned"])
        stored = commander.ack_state(self.coord, order["id"])
        self.assertEqual(stored["abandoned"]["by"], "love")
        self.assertIn("wedged", stored["abandoned"]["reason"])
        self.assertTrue(stored["abandoned"]["at"])

    def test_the_history_shows_a_decision_rather_than_a_timeout(self):
        order = _sent(self.coord)
        commander.abandon(self.coord, order["id"], actor="love", reason="wedged client")
        stored = next(row for row in commander._orders(Path(self.coord))
                      if row["id"] == order["id"])
        last = stored["attempts"][-1]
        self.assertEqual(last["state"], "ABANDONED")
        self.assertIn("love", last["note"])

    def test_a_reason_is_required(self):
        order = _sent(self.coord)
        with self.assertRaises(ValueError):
            commander.abandon(self.coord, order["id"], actor="love", reason="   ")

    def test_an_anonymous_abandonment_is_refused(self):
        order = _sent(self.coord)
        with self.assertRaises(ValueError):
            commander.abandon(self.coord, order["id"], actor="", reason="wedged client")

    def test_an_acknowledged_order_cannot_be_abandoned(self):
        # There is a real answer on record; "we gave up" would downgrade it.
        order = _sent(self.coord, state="AGENT_ACK")
        with self.assertRaises(ValueError):
            commander.abandon(self.coord, order["id"], actor="love", reason="wedged")

    def test_abandoning_twice_keeps_the_first_signature(self):
        order = _sent(self.coord)
        commander.abandon(self.coord, order["id"], actor="love", reason="first decision")
        again = commander.abandon(self.coord, order["id"], actor="someone-else",
                                  reason="a different story")
        self.assertTrue(again["already_abandoned"])
        self.assertEqual(again["abandoned"]["by"], "love")
        self.assertIn("first decision", again["abandoned"]["reason"])

    def test_an_unknown_order_is_an_error_not_a_silent_success(self):
        with self.assertRaises(ValueError):
            commander.abandon(self.coord, "2026-01-01T0000000000000000-deadbeef",
                              actor="love", reason="wedged")

    def test_abandoning_does_not_un_send_the_message(self):
        # Found by running the CLI against a hand-built order: the sign-off
        # overwrote state with ABANDONED and the verdict then reported
        # delivery "none", i.e. the abandonment had erased the send.
        order = _sent(self.coord)
        value = commander.abandon(self.coord, order["id"], actor="love",
                                  reason="the client is wedged")
        self.assertTrue(value["sent"])
        self.assertEqual(value["delivery"], "proven")
        stored = next(row for row in commander._orders(Path(self.coord))
                      if row["id"] == order["id"])
        self.assertEqual(stored["state_before_abandon"], "MESSAGE_VISIBLE")

    def test_abandoning_an_unsent_order_still_ends_the_wait(self):
        # Nothing was delivered, but the wait is over either way; the verdict
        # must not claim a send it never made.
        order = _sent(self.coord, state="DEPOSITED")
        value = commander.abandon(self.coord, order["id"], actor="love",
                                  reason="the task was withdrawn")
        self.assertEqual(value["state"], "abandoned")
        self.assertFalse(value["sent"])
        self.assertEqual(value["delivery"], "none")


if __name__ == "__main__":
    unittest.main()
