"""The gate that decides whether an order may be typed at all.

Every test here is a failure mode that produced a real wrong send or would have:
content edited after it was queued, two task ids in one body, an order addressed
to one client and bound to another's profile, an instruction that a newer order
already replaced.  The gate is pure, so each one is a fixture rather than a
desktop.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from agent_evolution_protocol.coordination import commander, dispatch_gate


PROFILE = {"agent": "qoder", "exe": r"D:\Programs\Qoder\Qoder.exe"}

#: The id ``wake_id()`` mints and that ``coord-send`` hands to ``submit_order``
#: as the order's event id.  Taken verbatim from the order that exposed the bug.
MAILBOX_ID = "20261102T073800053004Z-4971a758"


def _order(root, **kwargs):
    payload = {"target": "qoder", "task": "AEP-TASK-1",
               "text": "task AEP-TASK-1 for qoder\nworkspace: W-0123456789abcdef01234567\nAEP-ACK:EVT-AAAABBBBCCCC",
               "sender": "kernel", "workspace_id": "W-0123456789abcdef01234567",
               "event_id": "EVT-AAAABBBBCCCC"}
    payload.update(kwargs)
    return commander.submit_order(root, **payload)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _check(self, order, **kwargs):
        kwargs.setdefault("profile", PROFILE)
        return dispatch_gate.check(order, **kwargs)

    # -- the clean case ---------------------------------------------------
    def test_a_well_formed_order_is_allowed_and_reports_what_it_checked(self):
        order = _order(self.root)
        verdict = self._check(order)
        self.assertEqual(verdict["verdict"], "send")
        self.assertEqual(verdict["reason"], "")
        self.assertGreaterEqual(verdict["checked"], 8)
        self.assertEqual(verdict["content_sha256"], order["content_sha256"])

    # -- content identity -------------------------------------------------
    def test_text_edited_after_deposit_is_refused(self):
        order = dict(_order(self.root))
        order["text"] = order["text"].replace("AEP-TASK-1", "AEP-TASK-2")
        verdict = self._check(order)
        self.assertEqual(verdict["verdict"], "refuse")
        self.assertEqual(verdict["reason"], "content_unchanged")

    def test_two_event_ids_in_one_body_is_refused(self):
        order = _order(self.root)
        order["text"] = order["text"] + "\nstale: AEP-ACK:EVT-OLDOLDOLDOLD"
        # Re-record the hash so the failure is attributable to the extra id and
        # not to the edit: a gate that only catches the edit would let a body
        # built wrong from the start through.
        order["content_sha256"] = dispatch_gate.content_sha256(order["text"])
        verdict = self._check(order)
        self.assertEqual(verdict["verdict"], "refuse")
        self.assertEqual(verdict["reason"], "one_event_id")

    def test_a_foreign_task_marker_is_refused(self):
        order = _order(self.root)
        order["text"] = order["text"] + "\n[AEP:AEP-TASK-0] old instructions"
        order["content_sha256"] = dispatch_gate.content_sha256(order["text"])
        verdict = self._check(order)
        self.assertEqual(verdict["verdict"], "refuse")
        self.assertEqual(verdict["reason"], "one_task_marker")

    def test_an_order_with_an_envelope_but_no_hash_is_refused(self):
        order = _order(self.root)
        del order["content_sha256"]
        verdict = self._check(order)
        self.assertEqual(verdict["verdict"], "refuse")
        self.assertEqual(verdict["reason"], "content_hash_recorded")

    def test_a_legacy_order_without_an_envelope_is_warned_not_refused(self):
        order = {"id": "legacy-1", "target": "qoder", "task": "notice",
                 "text": "read your inbox\nAEP-ACK:EVT-LEGACY000001",
                 "event_id": "EVT-LEGACY000001", "created_at": "2026-10-31T00:00:00+00:00"}
        verdict = self._check(order)
        self.assertEqual(verdict["verdict"], "send")
        self.assertIn("content_hash_recorded", verdict["warned"])

    # -- addressing -------------------------------------------------------
    def test_an_order_bound_to_another_clients_profile_is_refused(self):
        order = _order(self.root)
        verdict = self._check(order, profile={"agent": "qoder_cn"})
        self.assertEqual(verdict["verdict"], "refuse")
        self.assertEqual(verdict["reason"], "target_matches_profile")
        self.assertIn("qoder_cn", verdict["detail"])

    def test_a_missing_ack_instruction_is_refused(self):
        order = _order(self.root)
        order["text"] = "task AEP-TASK-1 for qoder\nworkspace: W-0123456789abcdef01234567\nAEP-ACK:EVT-AAAABBBBCCCC"
        order["content_sha256"] = dispatch_gate.content_sha256(order["text"])
        verdict = self._check(order)
        self.assertEqual(verdict["verdict"], "send")
        order["text"] = order["text"].replace("AEP-ACK:", "")
        order["content_sha256"] = dispatch_gate.content_sha256(order["text"])
        verdict = self._check(order)
        self.assertEqual(verdict["verdict"], "refuse")
        self.assertEqual(verdict["reason"], "ack_requirement_in_text")

    # -- currency ---------------------------------------------------------
    def test_an_order_superseded_by_a_newer_one_is_refused(self):
        order = _order(self.root)
        newer = dict(order)
        newer["id"] = "newer-order-1"
        newer["event_id"] = "EVT-NEWNEWNEWNEW"
        newer["created_at"] = "2099-01-01T00:00:00+00:00"
        verdict = self._check(order, newer_orders=[order, newer])
        self.assertEqual(verdict["verdict"], "refuse")
        self.assertEqual(verdict["reason"], "not_superseded")

    def test_the_workspace_dag_has_the_last_word_on_the_event_id(self):
        order = _order(self.root)
        workspace = {"tasks": {"AEP-TASK-1": {"event_id": "EVT-DIFFERENT001"}}}
        verdict = self._check(order, workspace=workspace)
        self.assertEqual(verdict["verdict"], "refuse")
        self.assertEqual(verdict["reason"], "dag_event_matches")

    def test_an_expired_order_is_refused(self):
        order = _order(self.root, expires_minutes=1)
        order["expires_at"] = "2000-01-01T00:00:00+00:00"
        order["content_sha256"] = dispatch_gate.content_sha256(order["text"])
        verdict = self._check(order)
        self.assertEqual(verdict["verdict"], "refuse")
        self.assertEqual(verdict["reason"], "not_expired")

    # -- strictness -------------------------------------------------------
    def test_a_body_that_never_names_its_assignee_only_blocks_in_strict_mode(self):
        order = _order(self.root, text="do the thing\nAEP-ACK:EVT-AAAABBBBCCCC")
        lenient = self._check(order)
        self.assertEqual(lenient["verdict"], "send")
        self.assertIn("assignee_named_in_text", lenient["warned"])
        strict = self._check(order, strict=True)
        self.assertEqual(strict["verdict"], "refuse")
        self.assertIn("assignee_named_in_text", strict["failed"])

    def test_a_window_bound_only_by_process_name_is_a_warning_until_strict(self):
        order = _order(self.root)
        weak = self._check(order, window={"identity": "process-substring"})
        self.assertEqual(weak["verdict"], "send")
        self.assertIn("window_identity", weak["warned"])
        strict = self._check(order, window={"identity": "process-substring"}, strict=True)
        self.assertEqual(strict["verdict"], "refuse")
        self.assertEqual(strict["reason"], "window_identity")

    def test_an_absent_check_is_recorded_as_unchecked_not_as_a_pass(self):
        order = _order(self.root)
        verdict = self._check(order)
        by_id = {item["id"]: item for item in verdict["checks"]}
        self.assertFalse(by_id["dag_event_matches"]["checked"])
        self.assertTrue(by_id["dag_event_matches"]["ok"])
        self.assertTrue(by_id["content_unchanged"]["checked"])

    # -- preview ----------------------------------------------------------
    def test_the_preview_shows_the_ack_line_and_the_hash_it_will_compare(self):
        order = _order(self.root)
        seen = dispatch_gate.preview(order)
        self.assertEqual(seen["target"], "qoder")
        self.assertIn("AEP-ACK:", seen["ack_line"])
        self.assertEqual(seen["content_sha256"], order["content_sha256"])
        text = dispatch_gate.render_preview(order)
        self.assertIn("sha256", text)
        self.assertIn("| task AEP-TASK-1", text)


class CommanderRefusalTests(unittest.TestCase):
    """The gate is only real if the send path actually stops on it."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_a_tampered_order_fails_before_any_transport_is_chosen(self):
        order = _order(self.root)
        order["text"] = order["text"].replace("AEP-TASK-1", "AEP-TASK-9")
        # Tamper with the persisted copy, not just the in-memory dict: dispatch
        # refuses to act on a receipt that disagrees with the stored order, so a
        # test that only mutates the local copy proves nothing about the gate.
        commander._atomic(commander._state_path(self.root, order["id"]), order)
        commander._atomic(commander._root(self.root) / "inbox" / f"{order['id']}.json", order)
        profile = self.root / "ui_profiles" / "qoder.json"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text('{"agent": "qoder"}', encoding="utf-8")
        client = {"agent": "qoder", "windows": [{"pid": 7, "title": "Qoder",
                                                 "identity": "exe"}],
                  "unique": True, "anchored": False}
        from unittest.mock import patch
        with (patch.object(commander.registry, "discover", return_value={"clients": [client]}),
              patch.object(commander, "_pick_transport") as pick,
              patch.object(commander, "_profiled_deliver") as profiled,
              patch.object(commander, "_uia_deliver") as raw):
            result = commander.dispatch(self.root, order=order)
        self.assertEqual(result["state"], "FAILED")
        self.assertIn("DISPATCH_REFUSED", result["attempts"][-1]["note"])
        self.assertEqual(result["attempts"][-1]["receipt"]["signal"], "dispatch_refused")
        pick.assert_not_called()
        profiled.assert_not_called()
        raw.assert_not_called()

    def test_a_refusal_is_terminal_rather_than_retried_every_tick(self):
        order = _order(self.root)
        order["text"] = order["text"] + "\nstale AEP-ACK:EVT-OLDOLDOLDOLD"
        order["content_sha256"] = dispatch_gate.content_sha256(order["text"])
        commander._atomic(commander._state_path(self.root, order["id"]), order)
        commander._atomic(commander._root(self.root) / "inbox" / f"{order['id']}.json", order)
        profile = self.root / "ui_profiles" / "qoder.json"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text('{"agent": "qoder"}', encoding="utf-8")
        client = {"agent": "qoder", "windows": [{"pid": 7}], "unique": True, "anchored": False}
        from unittest.mock import patch
        with patch.object(commander.registry, "discover", return_value={"clients": [client]}):
            commander.dispatch(self.root, order=order)
        stored = next(row for row in commander._orders(self.root) if row["id"] == order["id"])
        self.assertEqual(stored["state"], "FAILED")
        # A retryable state would put it back in the queue and refuse it again
        # on every tick, which is a log flood wearing the costume of diligence.
        self.assertNotEqual(stored["state"], "DEFERRED")


class FingerprintGateTests(unittest.TestCase):
    """The session identity is checked before the content is even considered."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        profile = self.root / "clients" / "qoder.json"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(
            '{"agent": "qoder", "process": "Qoder", "title": "Qoder",'
            ' "exe": "D:\\\\Programs\\\\Qoder\\\\Qoder.exe",'
            ' "session_marker": "赛事夺冠执行", "model_marker": "DeepSeek-Flash",'
            ' "transport": "uia"}', encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _client(self):
        return {"agent": "qoder",
                "windows": [{"pid": 7, "title": "Qoder",
                             "exe": r"D:\Programs\Qoder\Qoder.exe",
                             "identity": "exe"}],
                "unique": True, "anchored": False}

    def test_a_recorded_fingerprint_from_another_conversation_defers_the_send(self):
        from agent_evolution_protocol.coordination import session_fingerprint
        session_fingerprint.record(self.root, "qoder", {
            "agent": "qoder", "pid": 7, "title": "Qoder",
            "exe": r"D:\Programs\Qoder\Qoder.exe",
            "session_marker": "另一个会话", "model_marker": "DeepSeek-Flash"})
        order = _order(self.root)
        from unittest.mock import patch
        with (patch.object(commander.registry, "discover", return_value={"clients": [self._client()]}),
              patch.object(commander, "_pick_transport") as pick,
              patch.object(commander, "_profiled_deliver") as profiled):
            result = commander.dispatch(self.root, order=order)
        self.assertEqual(result["state"], "DEFERRED")
        self.assertIn("SESSION_FINGERPRINT_MISMATCH", result["attempts"][-1]["note"])
        self.assertEqual(result["attempts"][-1]["receipt"]["session_fingerprint"]["diff"],
                         ["session_marker"])
        pick.assert_not_called()
        profiled.assert_not_called()

    def test_without_a_recorded_fingerprint_the_send_proceeds_and_says_so(self):
        order = _order(self.root)
        profile = self.root / "ui_profiles" / "qoder.json"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text('{"agent": "qoder"}', encoding="utf-8")
        from unittest.mock import patch
        with (patch.object(commander.registry, "discover", return_value={"clients": [self._client()]}),
              patch.object(commander, "_pick_transport", return_value=("uia", {})),
              patch.object(commander, "_profiled_deliver", return_value={"status": "NO_RECEIPT"})):
            result = commander.dispatch(self.root, order=order)
        found = next(attempt for attempt in result["attempts"]
                     if attempt["state"] == "WINDOW_FOUND")
        self.assertIsNone(found["receipt"]["session_fingerprint"]["match"])


class WakeIdContractTests(unittest.TestCase):
    """``coord-send --dispatch`` reuses the mailbox id as the order's event id.

    The gate only knew ``EVT-`` ids, so every such order refused itself at
    ``one_event_id`` before a single character was typed.  Measured on order
    ``2026-11-02T0738010285190000-f95f6aa7``: ``DISPATCH_REFUSED:one_event_id:
    found none`` for a body that carried its own id.  That check runs before any
    desktop input, so it said nothing about the UI and everything about the id
    vocabulary.  These tests hold the seam -- ``submit_order`` through
    ``dispatch`` -- because a regex unit test alone would have passed while the
    send path stayed broken.
    """

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _stamp_order(self):
        return commander.submit_order(
            self.root, target="qoder", task="AEP-TASK-1", text="please ack",
            sender="kernel", event_id=MAILBOX_ID)

    def test_the_mailbox_id_becomes_the_event_id_and_the_ack_token(self):
        order = self._stamp_order()
        self.assertEqual(order["event_id"], MAILBOX_ID)
        self.assertIn(f"AEP-ACK:{MAILBOX_ID}", order["text"])

    def test_a_mailbox_timestamp_id_is_one_event_id_not_none(self):
        verdict = dispatch_gate.check(self._stamp_order(), profile=PROFILE)
        self.assertEqual(verdict["verdict"], "send", verdict["detail"])
        self.assertEqual(verdict["reason"], "")

    def test_an_evt_id_is_still_accepted(self):
        verdict = dispatch_gate.check(_order(self.root), profile=PROFILE)
        self.assertEqual(verdict["verdict"], "send")

    def test_two_distinct_mailbox_ids_are_refused(self):
        order = self._stamp_order()
        order["text"] += "\nstale AEP-ACK:20261102T073800053005Z-4971a759"
        order["content_sha256"] = dispatch_gate.content_sha256(order["text"])
        verdict = dispatch_gate.check(order, profile=PROFILE)
        self.assertEqual(verdict["reason"], "one_event_id")

    def test_a_mailbox_id_that_is_not_this_orders_is_refused(self):
        order = self._stamp_order()
        order["event_id"] = "20261102T073800053005Z-4971a759"
        verdict = dispatch_gate.check(order, profile=PROFILE)
        self.assertEqual(verdict["reason"], "one_event_id")

    def test_one_evt_and_one_mailbox_id_is_two_ids(self):
        order = self._stamp_order()
        order["text"] += "\nAEP-ACK:EVT-AAAABBBBCCCC"
        order["content_sha256"] = dispatch_gate.content_sha256(order["text"])
        verdict = dispatch_gate.check(order, profile=PROFILE)
        self.assertEqual(verdict["reason"], "one_event_id")

    def test_is_stamp_id_separates_the_two_vocabularies(self):
        self.assertTrue(dispatch_gate.is_stamp_id(MAILBOX_ID))
        self.assertFalse(dispatch_gate.is_stamp_id("EVT-AAAABBBBCCCC"))
        # Uppercase hex is not this form; the pattern stays exact so a near-miss
        # cannot be accepted as an id.
        self.assertFalse(dispatch_gate.is_stamp_id("20261102T073800053004Z-4971A758"))
        self.assertFalse(dispatch_gate.is_stamp_id(""))
        self.assertFalse(dispatch_gate.is_stamp_id(None))

    def test_dispatch_no_longer_refuses_a_mailbox_id_order(self):
        """The reported failure, end to end through the real send path."""
        order = self._stamp_order()
        profile = self.root / "ui_profiles" / "qoder.json"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text('{"agent": "qoder"}', encoding="utf-8")
        client = {"agent": "qoder", "windows": [{"pid": 7, "title": "Qoder",
                                                 "identity": "exe"}],
                  "unique": True, "anchored": False}
        from unittest.mock import patch
        with (patch.object(commander.registry, "discover", return_value={"clients": [client]}),
              patch.object(commander, "_pick_transport", return_value=("uia", {})),
              patch.object(commander, "_uia_deliver", return_value={"status": "NO_RECEIPT"})):
            result = commander.dispatch(self.root, order=order)
        notes = [str(attempt.get("note", "")) for attempt in result.get("attempts", [])]
        self.assertFalse(any("DISPATCH_REFUSED" in note for note in notes),
                         f"gate refused a mailbox id order: {notes}")


class CoordSendMappingTests(unittest.TestCase):
    """``coord-send --dispatch`` must leave a durable link to the order it made.

    The mailbox message and the order live in two roots.  Printing the mapping
    only to stdout meant a reader of the message could not find the order, which
    is how the FlagGems and AEP roots stayed unreconciled.
    """

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_the_stored_message_names_the_order_and_the_id_it_was_asked_to_echo(self):
        from argparse import Namespace
        from unittest.mock import patch
        from agent_evolution_protocol import cli

        coord = self.root / "coordination"
        args = Namespace(sender="kernel", to="qoder", text="please ack",
                         text_file=None, topic="AEP-TASK-1", kind="notice",
                         dispatch=True, mailbox_only=False, allow_launch=False,
                         coord_root=str(coord))
        with patch.object(cli.commander, "dispatch", return_value={"state": "DEFERRED"}):
            cli.coord_send(args)
        stored = json.loads(next((coord / "messages" / "qoder").glob("*.json"))
                            .read_text(encoding="utf-8"))
        delivery = stored["desktop_delivery"]
        self.assertEqual(delivery["state"], "DEFERRED")
        # The mailbox id is reused as the order's event id, so this one value is
        # both the join key between the two roots and the token the target must
        # echo in its ACK.
        self.assertEqual(delivery["event_id"], stored["id"])
        # Resolved through ``commander._root`` rather than hardcoding the
        # directory: the point of the mapping is that the two roots differ.
        order = json.loads((commander._root(coord) / "inbox" / f"{delivery['order_id']}.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(order["event_id"], stored["id"])
        self.assertEqual(dispatch_gate.check(order, profile=PROFILE)["verdict"], "send")


if __name__ == "__main__":
    unittest.main()
