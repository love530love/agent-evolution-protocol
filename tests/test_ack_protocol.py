"""An ACK is an event with a role, not a string in a transcript.

The token we ask for is written into the message we send, so every naive
detector finds it immediately -- in our own line.  Each test below is one of the
guards that stops that from being reported as acceptance.
"""

from datetime import datetime, timedelta, timezone
import unittest

from agent_evolution_protocol.coordination import ack_protocol


NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
SENT_AT = (NOW - timedelta(minutes=5)).isoformat()


def _order(**extra):
    payload = {"id": "order-1", "target": "qoder", "task": "AEP-TASK-1",
               "event_id": "EVT-AAAABBBBCCCC", "session_id": "4242",
               "created_at": SENT_AT, "state": "MESSAGE_VISIBLE", "attempts": []}
    payload.update(extra)
    return payload


def _event(text, *, role="assistant", direction="incoming", session_id="4242",
           recorded_at=None, event_id="evt-1"):
    return {"id": event_id, "role": role, "direction": direction,
            "session_id": session_id,
            "recorded_at": recorded_at or NOW.isoformat(),
            "added_lines": [text]}


TOKEN = "AEP-ACK:EVT-AAAABBBBCCCC"


class AckVerdictTests(unittest.TestCase):
    def test_a_role_attributed_reply_is_accepted(self):
        value = ack_protocol.verdict(_order(), events=[_event(f"done {TOKEN}")], now=NOW)
        self.assertEqual(value["state"], "acked")
        self.assertTrue(value["accepted"])
        self.assertTrue(ack_protocol.may_advance(value))
        self.assertEqual(value["evidence"]["event"], "evt-1")

    def test_our_own_request_is_not_an_answer(self):
        # The outgoing message contains the token because it *asks* for it.
        value = ack_protocol.verdict(
            _order(), events=[_event(f"please reply {TOKEN}", role="unknown", direction="unknown")],
            now=NOW)
        self.assertFalse(value["accepted"])
        self.assertEqual(value["state"], "sent_unacked")
        self.assertEqual(value["reason"], "not_assistant")

    def test_a_line_captured_as_outgoing_is_not_an_answer(self):
        value = ack_protocol.verdict(
            _order(), events=[_event(TOKEN, role="assistant", direction="outgoing")], now=NOW)
        self.assertFalse(value["accepted"])
        self.assertEqual(value["reason"], "not_incoming")

    def test_a_composer_draft_is_not_an_answer(self):
        # A draft published as a user/outgoing line is the "fake ACK in the
        # input box" case: the text is right and the sender is us.
        value = ack_protocol.verdict(
            _order(), events=[_event(f"draft {TOKEN}", role="user", direction="outgoing")],
            now=NOW)
        self.assertFalse(value["accepted"])
        self.assertEqual(value["reason"], "not_assistant")

    def test_another_conversation_answering_is_not_this_task(self):
        value = ack_protocol.verdict(
            _order(), events=[_event(TOKEN, session_id="9999")], now=NOW)
        self.assertFalse(value["accepted"])
        self.assertEqual(value["reason"], "foreign_session")

    def test_an_old_reply_cannot_satisfy_a_new_delivery(self):
        # Event ids are stable per task, so a re-delivery reuses the id and the
        # previous answer is still in the transcript.
        old = (NOW - timedelta(hours=2)).isoformat()
        value = ack_protocol.verdict(
            _order(created_at=NOW.isoformat()),
            events=[_event(TOKEN, recorded_at=old)], now=NOW)
        self.assertFalse(value["accepted"])
        self.assertEqual(value["reason"], "stale")

    def test_a_late_reply_is_accepted(self):
        late = (NOW + timedelta(hours=3)).isoformat()
        value = ack_protocol.verdict(
            _order(created_at=SENT_AT), events=[_event(TOKEN, recorded_at=late)], now=NOW)
        self.assertTrue(value["accepted"])
        # No timeout state exists: a slow answer is an answer, and treating it
        # as refusal is what produces duplicate sends.
        self.assertNotIn("timeout", value["state"])

    def test_a_missing_session_id_on_the_order_cannot_be_satisfied(self):
        value = ack_protocol.verdict(
            _order(session_id=""), events=[_event(TOKEN)], now=NOW)
        self.assertFalse(value["accepted"])
        self.assertEqual(value["reason"], "foreign_session")

    def test_nothing_sent_yet_is_not_sent_rather_than_unacked(self):
        value = ack_protocol.verdict(
            _order(state="DEPOSITED"), events=[], now=NOW)
        self.assertEqual(value["state"], "not_sent")
        self.assertFalse(value["sent"])

    def test_sent_without_any_reply_is_unacked(self):
        value = ack_protocol.verdict(_order(state="DEPOSITED",
                                            attempts=[{"state": "MESSAGE_VISIBLE"}]),
                                     events=[], now=NOW)
        self.assertEqual(value["state"], "sent_unacked")
        self.assertTrue(value["sent"])

    def test_every_rejection_is_kept_so_the_cause_is_visible(self):
        value = ack_protocol.verdict(
            _order(),
            events=[_event(f"ours {TOKEN}", role="unknown"),
                    _event(TOKEN, session_id="9999", event_id="evt-2")],
            now=NOW)
        self.assertEqual([row["reason"] for row in value["rejected"]],
                         ["not_assistant", "foreign_session"])
        self.assertEqual(value["candidates"], 2)

    def test_an_event_without_the_token_is_ignored_entirely(self):
        value = ack_protocol.verdict(_order(), events=[_event("unrelated chatter")], now=NOW)
        self.assertEqual(value["candidates"], 0)
        self.assertEqual(value["reason"], "")

    def test_token_requires_an_event_id(self):
        with self.assertRaises(ValueError):
            ack_protocol.token_for("")
        self.assertEqual(ack_protocol.token_for("EVT-1"), "AEP-ACK:EVT-1")

    def test_may_advance_rejects_anything_that_is_not_an_accepted_verdict(self):
        self.assertFalse(ack_protocol.may_advance({}))
        self.assertFalse(ack_protocol.may_advance({"schema": ack_protocol.SCHEMA,
                                                   "accepted": False}))
        self.assertFalse(ack_protocol.may_advance({"accepted": True}))
        self.assertTrue(ack_protocol.may_advance({"schema": ack_protocol.SCHEMA,
                                                  "accepted": True}))


if __name__ == "__main__":
    unittest.main()
