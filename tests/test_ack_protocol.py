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


class CeasedIsNotOneFactTests(unittest.TestCase):
    """``CEASED`` covers two opposite situations and only the note tells them apart.

    Found on a real order (``2026-10-30T0422243473450000-04d70400``) whose last
    attempt read ``CEASED / SUBMISSION_UNCONFIRMED: possible send; do not retry
    automatically`` while the verdict said ``not_sent``.  One of those two
    statements had to be false.
    """

    def _ceased(self, note, **extra):
        return _order(state="CEASED", attempts=[{"state": "CEASED", "note": note}], **extra)

    def test_a_possible_send_is_neither_sent_nor_not_sent(self):
        order = self._ceased("SUBMISSION_UNCONFIRMED: possible send; do not retry automatically")
        self.assertEqual(ack_protocol.delivery(order), "unconfirmed")
        value = ack_protocol.verdict(order, events=[], now=NOW)
        self.assertEqual(value["state"], "submitted_unconfirmed")
        self.assertEqual(value["delivery"], "unconfirmed")
        # Not proven, so the supervisor must not wait for a reply to a message
        # that may never have been asked for.
        self.assertFalse(value["sent"])
        self.assertFalse(value["accepted"])

    def test_an_adapter_that_failed_before_typing_is_still_not_sent(self):
        order = self._ceased("DELIVERY_UNPROVEN: adapter failed; manual diagnosis required")
        self.assertEqual(ack_protocol.delivery(order), "none")
        self.assertEqual(ack_protocol.verdict(order, events=[], now=NOW)["state"], "not_sent")

    def test_proof_beats_a_later_unconfirmed_note(self):
        # An earlier attempt that reached the conversation settles the question:
        # the later failure does not un-send it.
        order = _order(state="CEASED", attempts=[
            {"state": "INPUT_SUBMITTED"},
            {"state": "CEASED", "note": "SUBMISSION_UNCONFIRMED: possible send"},
        ])
        self.assertEqual(ack_protocol.delivery(order), "proven")
        self.assertEqual(ack_protocol.verdict(order, events=[], now=NOW)["state"], "sent_unacked")

    def test_the_note_is_matched_as_a_prefix_so_it_can_gain_explanation(self):
        order = self._ceased("SUBMISSION_UNCONFIRMED: possible send, and the composer still "
                             "held text -- a human should look before anything is retried")
        self.assertEqual(ack_protocol.delivery(order), "unconfirmed")

    def test_an_unrelated_ceased_note_does_not_claim_a_send(self):
        order = self._ceased("")
        self.assertEqual(ack_protocol.delivery(order), "none")

    def test_delivery_rejects_a_non_object(self):
        with self.assertRaises(ValueError):
            ack_protocol.delivery(["not", "an", "order"])


class NotePrefixesAreStillWhatCommanderWritesTests(unittest.TestCase):
    """The two ``CEASED`` notes are the only thing separating "may have sent"
    from "nothing was typed".  If ``commander`` renames one, this module keeps
    returning ``unconfirmed`` for a string nobody writes -- i.e. it silently
    starts saying ``not_sent`` again, which is the defect this fixed.  So the
    coupling is asserted instead of assumed.
    """

    def test_the_prefixes_appear_in_commander(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "src" / "agent_evolution_protocol"
                  / "coordination" / "commander.py").read_text(encoding="utf-8")
        for prefix in (ack_protocol.UNCONFIRMED_NOTE, ack_protocol.UNPROVEN_NOTE):
            self.assertIn(f'"{prefix}:', source,
                          f"commander no longer writes a note starting with {prefix}")


if __name__ == "__main__":
    unittest.main()
