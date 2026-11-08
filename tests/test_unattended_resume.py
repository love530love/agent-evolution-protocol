"""The order must keep moving while nobody is watching.

A deferral that proves nothing was typed is a "not yet", not a failure.  The
three things that used to stop the loop were: such a deferral was never
re-examined unless it came from a busy client, the OCR composer check could not
confirm a paste because it searched for an unreadable event id, and the reply
challenge was a hex string the recogniser never reproduced.  Each of those is
covered here.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agent_evolution_protocol.coordination import commander
from agent_evolution_protocol.coordination import commander_base
from agent_evolution_protocol.coordination import commander_uia
from agent_evolution_protocol.coordination import ui_fallback as ui


PROFILE = {"agent": "fake_agent", "process": "FakeAgent", "title": "Fake Agent",
           "exe": str((Path.cwd() / "FakeAgent.exe").resolve()),
           "session_marker": "Original task chat", "model_marker": "Model X",
           "transport": "ocr", "reply_timeout_seconds": 5, "send_mode": "enter",
           "allow_busy_interject": False, "interject_hints": ["Interject"]}


class ComposerNotConfirmedDriver:
    """Says the paste never appeared, twice in a row, then succeeds."""

    def __init__(self, fail_times=1):
        self.fail_times = fail_times
        self.deliveries = 0

    def observe(self, profile):
        return {"status": "window-observed", "pid": 4242, "activity_state": "ready",
                "input_found": True}

    def capture(self, profile):
        return "Original task chat\nModel X\n"

    def select_session(self, profile):
        return {"status": "session-selection-attempted"}

    def launch(self, profile):
        raise AssertionError("must not launch")

    def deliver(self, profile, message, event_id, *, urgent=False, composer_ratios=None):
        self.deliveries += 1
        if self.deliveries <= self.fail_times:
            return {"status": "composer-not-confirmed", "task_delivered": False,
                    "note": "the message never appeared in the window; refusing to press Enter"}
        return {"status": "message-visible", "task_delivered": True}


class ComposerNotConfirmedTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.receipt = self.folder / "receipt.json"
        self.lock = self.folder / "desktop.lock"

    def tearDown(self):
        self.temp.cleanup()

    def test_the_state_is_named_for_what_it_proves(self):
        driver = ComposerNotConfirmedDriver()
        result = ui.run(PROFILE, "Analyze the error", event_id="EVT-COMPOSER-1",
                        receipt_path=self.receipt, lock_path=self.lock,
                        driver=driver, poll_seconds=0.1)
        self.assertEqual(result["state"], "COMPOSER_NOT_CONFIRMED")
        self.assertFalse(result["task_delivered"])
        self.assertEqual(result["send_report"]["status"], "composer-not-confirmed")

    def test_the_same_event_id_is_retried_instead_of_ceasing(self):
        # Nothing was submitted, so reusing the event id cannot duplicate a
        # message.  Before this, the receipt said SEND_UNCERTAIN and the order
        # ceased, which is how an unattended run stopped for good.
        driver = ComposerNotConfirmedDriver()
        first = ui.run(PROFILE, "Analyze the error", event_id="EVT-COMPOSER-2",
                       receipt_path=self.receipt, lock_path=self.lock,
                       driver=driver, poll_seconds=0.1)
        self.assertEqual(first["state"], "COMPOSER_NOT_CONFIRMED")
        self.assertTrue(ui._preinput_receipt_retryable(first))
        second = ui.run(PROFILE, "Analyze the error", event_id="EVT-COMPOSER-2",
                        receipt_path=self.receipt, lock_path=self.lock,
                        driver=driver, poll_seconds=0.1)
        # The second attempt delivered; it then timed out waiting for a reply,
        # which is a different fact and does not make the retry wrong.
        self.assertEqual(second["state"], "NO_REPLY_WITHIN_TIMEOUT")
        self.assertTrue(second["task_delivered"])
        self.assertEqual(driver.deliveries, 2)


class AutoResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _deferred_order(self, *, status="AGENT_BUSY", deferrals=1):
        order = commander.submit_order(self.root, target="workbuddy3", task="notice",
                                       text="Read inbox", sender="codex")
        commander._record_attempt(self.root, order, state="WINDOW_FOUND")
        for _ in range(deferrals):
            receipt = {"status": status, "task_delivered": False}
            if status in {"AGENT_BUSY", "ACTIVITY_UNCONFIRMED"}:
                # What _profiled_deliver records for a client that is still
                # working: cheap read-only readiness polling, not a retry.
                receipt["wait_for_ready"] = True
            commander._record_attempt(self.root, order, state="DEFERRED", receipt=receipt)
        return order

    def test_deferral_count_does_not_reset_when_the_retry_is_recorded(self):
        order = self._deferred_order(deferrals=2)
        commander._record_attempt(self.root, order, state="DEPOSITED", note="AUTO_PREINPUT_RESUME")
        self.assertEqual(commander._deferred_count(order), 2)

    def test_a_proven_pre_input_deferral_becomes_due(self):
        order = self._deferred_order(status="COMPOSER_NOT_CONFIRMED")
        with patch.object(commander_base, "AUTO_RESUME_BACKOFF_SECONDS", 0):
            self.assertTrue(commander_base._auto_resume_due(order))

    def test_the_budget_is_bounded(self):
        order = self._deferred_order(status="COMPOSER_NOT_CONFIRMED",
                                     deferrals=commander_base.AUTO_RESUME_LIMIT)
        with patch.object(commander_base, "AUTO_RESUME_BACKOFF_SECONDS", 0):
            self.assertFalse(commander_base._auto_resume_due(order))

    def test_an_uncertain_history_is_never_resumed(self):
        order = commander_base.submit_order(self.root, target="workbuddy3", task="notice",
                                            text="Read inbox", sender="codex")
        # WINDOW_FOUND with no later pre-input proof: the message may have been
        # typed, so the no-replay fence has to hold.
        commander_base._record_attempt(self.root, order, state="WINDOW_FOUND")
        commander_base._record_attempt(self.root, order, state="DEFERRED",
                                       receipt={"status": "unknown-status", "task_delivered": False})
        with patch.object(commander_base, "AUTO_RESUME_BACKOFF_SECONDS", 0):
            self.assertFalse(commander_base._auto_resume_due(order))

    def test_the_backoff_holds_a_fresh_deferral(self):
        order = self._deferred_order(status="COMPOSER_NOT_CONFIRMED")
        self.assertFalse(commander._auto_resume_due(order))

    def test_tick_redeposits_a_pre_input_deferral_on_its_own(self):
        order = self._deferred_order(status="COMPOSER_NOT_CONFIRMED")
        with patch.object(commander_base, "AUTO_RESUME_BACKOFF_SECONDS", 0), \
                patch.object(commander, "dispatch",
                             return_value={"state": "DEPOSITED"}) as dispatch:
            commander.tick(self.root, perceive_first=False)
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.kwargs["order"]["event_id"], order["event_id"])
        notes = [attempt.get("note", "") for attempt in commander_base._orders(self.root)[0]["attempts"]]
        self.assertTrue(any("AUTO_PREINPUT_RESUME" in note for note in notes))

    def test_a_busy_deferral_keeps_using_the_cheap_readiness_probe(self):
        # A busy client is polled read-only; retrying it would cost a full UI
        # attempt and fight the run it is waiting for.
        order = self._deferred_order(status="AGENT_BUSY")
        with patch.object(commander_base, "AUTO_RESUME_BACKOFF_SECONDS", 0), \
                patch.object(commander, "_profiled_readiness",
                             return_value={"status": "window-observed",
                                           "activity_state": "busy"}) as readiness, \
                patch.object(commander, "dispatch") as dispatch:
            commander.tick(self.root, perceive_first=False)
        readiness.assert_called_once()
        dispatch.assert_not_called()
        self.assertEqual(order["event_id"], commander_base._orders(self.root)[0]["event_id"])


class ReplyProofTests(unittest.TestCase):
    def test_an_ocr_challenge_is_digits_so_the_reply_can_be_read(self):
        nonce, expected = ui._challenge(ocr_safe=True)
        self.assertTrue(nonce.isdigit())
        self.assertEqual(len(nonce), 10)
        self.assertNotEqual(nonce, nonce[::-1])
        self.assertEqual(expected, "AEP-ACK:" + nonce[::-1])

    def test_the_hex_challenge_is_kept_for_clients_that_can_read_it(self):
        nonce, expected = ui._challenge()
        self.assertTrue(all(character in "0123456789ABCDEF" for character in nonce))
        self.assertEqual(expected, "AEP-ACK:" + nonce[::-1])

    def test_a_reversed_nonce_counts_only_when_it_is_new(self):
        before = "Original task chat\nModel X\n"
        nonce = "1234567890"
        self.assertEqual(ui._ack_seen(before + "AEP-ACK:0987654321", "AEP-ACK:0987654321",
                                      nonce[::-1], before), "exact")
        # The prefix is what the recogniser mangles; the digits are what it
        # reads, so the digits alone are accepted -- but only when they were
        # not already on screen.
        self.assertEqual(ui._ack_seen(before + "0987654321", "AEP-ACK:0987654321",
                                      nonce[::-1], before), "reversed-nonce")
        self.assertEqual(ui._ack_seen(before + "0987654321", "AEP-ACK:0987654321",
                                      nonce[::-1], before + "0987654321"), "")

    def test_the_reply_is_verified_through_the_reversed_digits(self):
        class AnsweringDriver(ComposerNotConfirmedDriver):
            def __init__(self):
                super().__init__(fail_times=0)
                self.nonce = ""

            def deliver(self, profile, message, event_id, *, urgent=False, composer_ratios=None):
                self.deliveries += 1
                marker = "校验串 "
                start = message.index(marker) + len(marker)
                self.nonce = message[start:start + 10]
                return {"status": "message-visible", "task_delivered": True}

            def capture(self, profile):
                # Only the digits survive the recogniser.
                return f"Original task chat\nModel X\n{self.nonce[::-1]}\n"

        with TemporaryDirectory() as folder:
            driver = AnsweringDriver()
            result = ui.run(PROFILE, "Analyze the error", event_id="EVT-REPLY-1",
                            receipt_path=Path(folder) / "receipt.json",
                            lock_path=Path(folder) / "desktop.lock",
                            driver=driver, poll_seconds=0.1)
        self.assertEqual(result["state"], "AGENT_REPLY_VERIFIED")
        self.assertEqual(result["ack_evidence"], "reversed-nonce")
        self.assertTrue(result["agent_accepted"])


if __name__ == "__main__":
    unittest.main()
