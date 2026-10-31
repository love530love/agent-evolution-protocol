import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agent_evolution_protocol.coordination import ui_fallback as ui
from agent_evolution_protocol.coordination import wake_daemon


PROFILE = {"agent": "fake_agent", "process": "FakeAgent", "title": "Fake Agent",
           "exe": str((Path.cwd() / "FakeAgent.exe").resolve()), "session_marker": "Original task chat",
           "model_marker": "Model X", "transport": "uia", "reply_timeout_seconds": 5,
           "send_mode": "enter", "allow_busy_interject": False,
           "interject_hints": ["Interject"]}


class FakeDriver:
    def __init__(self, *, model=True, session=True, input_found=True, no_window=False,
                 send_status="submitted-unconfirmed", activity_state="ready",
                 interject_ready=False, interruption=False, drivable=None,
                 blocked_by=None, answers=True, delivered=None):
        self.model = model
        self.session = session
        self.input_found = input_found
        self.no_window = no_window
        self.send_status = send_status
        self.activity_state = activity_state
        self.interject_ready = interject_ready
        self.interruption = interruption
        self.drivable = drivable
        self.blocked_by = blocked_by
        self.answers = answers
        self.delivered = delivered
        self.sent = 0
        self.launched = 0
        self.selected = 0
        self.captured = 0
        self.expected_ack = ""

    def observe(self, profile):
        if self.no_window:
            return {"status": "NO_WINDOW"}
        report = {"status": "window-observed", "pid": 1234, "input_found": self.input_found,
                  "input_ambiguous": False, "activity_state": self.activity_state,
                  "interject_evidence": {"ready": self.interject_ready,
                                         "interrupt_only": False}}
        if self.drivable is not None:
            report["drivable"] = self.drivable
            report["target_elevation"] = "unknown" if not self.drivable else "standard"
            report["executor_elevation"] = "standard"
            if self.blocked_by:
                report["blocked_by"] = self.blocked_by
        return report

    def capture(self, profile):
        self.captured += 1
        text = ("Original task chat " if self.session else "Other chat ")
        text += "Model X" if self.model else "Different model"
        if self.expected_ack:
            text += "\n" + self.expected_ack + "\nI found a new approach."
        return text

    def select_session(self, profile):
        self.selected += 1
        self.session = True
        return {"status": "session-selection-attempted"}

    def launch(self, profile):
        self.launched += 1
        self.no_window = False

    def deliver(self, profile, message, event_id, *, urgent=False):
        self.sent += 1
        if not self.answers:
            return {"status": self.send_status,
                    "task_delivered": self.delivered is True,
                    "interruption": self.interruption,
                    "interject_action": "Interject" if urgent else ""}
        import re
        match = re.search(r"校验串 ([0-9A-F]+) 的倒序", message)
        assert match
        self.expected_ack = "AEP-ACK:" + match.group(1)[::-1]
        assert self.expected_ack not in message
        return {"status": self.send_status,
                "interruption": self.interruption,
                "interject_action": "Interject" if urgent else ""}


class UiFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.receipt = self.folder / "receipt.json"
        self.lock = self.folder / "desktop.lock"

    def tearDown(self):
        self.temp.cleanup()

    def run_relay(self, driver, **kwargs):
        return ui.run(PROFILE, "Analyze the error", event_id="EVT-12345",
                      receipt_path=self.receipt, lock_path=self.lock,
                      driver=driver, poll_seconds=0.1, **kwargs)

    def test_wrong_model_never_sends(self):
        driver = FakeDriver(model=False)
        result = self.run_relay(driver)
        self.assertEqual(result["state"], "MODEL_NOT_VERIFIED")
        self.assertEqual(driver.sent, 0)

    def test_unverified_session_names_the_marker_it_looked_for(self):
        # "session not verified" and "the profile marker is a typo" look
        # identical from outside unless the receipt says which string was
        # required.  Measured live: a profile said 赛事守冠执行 while the window
        # showed 赛事夺冠执行, and every scheduled delivery deferred forever.
        profile = {**PROFILE, "session_marker": "Original task chatX"}
        driver = FakeDriver(session=False)
        result = ui.run(profile, "Analyze the error", event_id="EVT-MARKER-1",
                        receipt_path=self.receipt, lock_path=self.lock,
                        driver=driver, poll_seconds=0.1)
        self.assertEqual(result["state"], "SESSION_NOT_VERIFIED")
        self.assertEqual(result["session_marker"], "Original task chatX")
        self.assertFalse(result["session_marker_present"])
        self.assertGreater(result["captured_chars"], 0)
        self.assertEqual(driver.sent, 0)

    def test_selects_original_session_then_confirms_distinct_reply(self):
        driver = FakeDriver(session=False)
        result = self.run_relay(driver)
        self.assertEqual(driver.selected, 1)
        self.assertEqual(driver.sent, 1)
        self.assertEqual(result["state"], "AGENT_REPLY_VERIFIED")
        self.assertTrue(result["agent_accepted"])
        self.assertIn("I found a new approach", result["reply_excerpt"])

    def test_uncertain_send_is_never_automatically_retried(self):
        driver = FakeDriver(send_status="input-not-confirmed")
        first = self.run_relay(driver)
        second = self.run_relay(driver)
        self.assertEqual(first["state"], "SEND_UNCERTAIN")
        self.assertEqual(second, first)
        self.assertEqual(driver.sent, 1)

    def test_existing_window_required_unless_launch_is_explicit(self):
        driver = FakeDriver(no_window=True)
        result = self.run_relay(driver)
        self.assertEqual(result["state"], "NO_WINDOW")
        self.assertEqual(driver.launched, 0)
        self.receipt.unlink()
        result = self.run_relay(driver, allow_launch=True)
        self.assertEqual(driver.launched, 1)
        self.assertEqual(result["state"], "AGENT_REPLY_VERIFIED")

    def test_opaque_client_requires_exact_ocr_hint(self):
        profile_path = self.folder / "profile.json"
        profile_path.write_text(json.dumps({**PROFILE, "transport": "ocr"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            ui.load_profile(profile_path)

    def test_profile_enables_generic_wake_adapter(self):
        root = self.folder / "repo"
        fake_module_root = root / "src" / "agent_evolution_protocol" / "coordination"
        profile = root / "coordination" / "ui_profiles" / "fake_agent.json"
        profile.parent.mkdir(parents=True)
        profile.write_text(json.dumps(PROFILE), encoding="utf-8")
        with patch.object(wake_daemon, "ROOT", fake_module_root):
            selected = wake_daemon.adapter_for("fake_agent")
        self.assertEqual(selected.name, "ui_fallback.ps1")

    def test_ocr_profile_loads_only_with_exact_hint(self):
        profile_path = self.folder / "ocr.json"
        profile_path.write_text(json.dumps({**PROFILE, "transport": "ocr",
                                            "composer_hint": "Ask anything"}), encoding="utf-8")
        self.assertEqual(ui.load_profile(profile_path)["composer_hint"], "Ask anything")

    def test_busy_agent_is_deferred_before_any_input(self):
        profile = {**PROFILE, "require_activity_ready": True}
        driver = FakeDriver(activity_state="busy")
        result = ui.run(profile, "Analyze the error", event_id="EVT-BUSY-1",
                         receipt_path=self.receipt, lock_path=self.lock,
                         driver=driver, poll_seconds=0.1)
        self.assertEqual(result["state"], "AGENT_BUSY")
        self.assertEqual(driver.sent, 0)

    def test_activity_uncertainty_is_deferred_for_strict_profile(self):
        profile = {**PROFILE, "require_activity_ready": True}
        driver = FakeDriver(activity_state="unknown")
        result = ui.run(profile, "Analyze the error", event_id="EVT-ACTIVITY-1",
                         receipt_path=self.receipt, lock_path=self.lock,
                         driver=driver, poll_seconds=0.1)
        self.assertEqual(result["state"], "ACTIVITY_UNCONFIRMED")
        self.assertEqual(driver.sent, 0)

    def test_preinput_activity_receipt_can_retry_after_ready(self):
        profile = {**PROFILE, "require_activity_ready": True}
        driver = FakeDriver(activity_state="unknown")
        first = ui.run(profile, "Analyze the error", event_id="EVT-ACTIVITY-RETRY-1",
                        receipt_path=self.receipt, lock_path=self.lock,
                        driver=driver, poll_seconds=0.1)
        self.assertEqual(first["state"], "ACTIVITY_UNCONFIRMED")
        driver.activity_state = "ready"
        second = ui.run(profile, "Analyze the error", event_id="EVT-ACTIVITY-RETRY-1",
                         receipt_path=self.receipt, lock_path=self.lock,
                         driver=driver, poll_seconds=0.1)
        self.assertEqual(second["state"], "AGENT_REPLY_VERIFIED")
        self.assertEqual(driver.sent, 1)

    def test_enter_is_the_profile_default(self):
        self.assertEqual(PROFILE["send_mode"], "enter")

    def test_a_visible_message_counts_as_delivered_even_without_a_reply(self):
        # Measured on the scheduler engine: send_status message-visible, then
        # the reply never came, and the receipt still said task_delivered false.
        # Delivered and accepted are separate claims and must not collapse.
        profile = {**PROFILE, "reply_timeout_seconds": 0.2}
        driver = FakeDriver(send_status="message-visible", answers=False, delivered=True)
        result = ui.run(profile, "Analyze the error", event_id="EVT-NOREPLY-1",
                        receipt_path=self.receipt, lock_path=self.lock,
                        driver=driver, poll_seconds=0.1)
        self.assertEqual(result["state"], "NO_REPLY_WITHIN_TIMEOUT")
        self.assertTrue(result["task_delivered"])
        self.assertFalse(result["agent_accepted"])

    def test_an_unconfirmed_send_is_not_promoted_to_delivered(self):
        profile = {**PROFILE, "reply_timeout_seconds": 0.2}
        driver = FakeDriver(send_status="submitted-unconfirmed", answers=False, delivered=False)
        result = ui.run(profile, "Analyze the error", event_id="EVT-UNCONF-1",
                        receipt_path=self.receipt, lock_path=self.lock,
                        driver=driver, poll_seconds=0.1)
        self.assertEqual(result["state"], "NO_REPLY_WITHIN_TIMEOUT")
        self.assertFalse(result["task_delivered"])
        self.assertFalse(result["agent_accepted"])

    def test_undrivable_window_is_refused_before_any_ui_work(self):
        # An unelevated executor pointed at an elevated client used to spend the
        # full 75-second attempt and then record "possible send".  UIPI is not a
        # race: nothing can have been typed, so refuse before capturing.
        driver = FakeDriver(drivable=False, blocked_by="TARGET_NEEDS_ELEVATION")
        result = ui.run(PROFILE, "Analyze the error", event_id="EVT-ELEV-1",
                        receipt_path=self.receipt, lock_path=self.lock,
                        driver=driver, poll_seconds=0.1)
        self.assertEqual(result["state"], "EXECUTOR_TOO_LOW")
        self.assertEqual(result["blocked_by"], "TARGET_NEEDS_ELEVATION")
        self.assertEqual((result["target_elevation"], result["executor_elevation"]),
                         ("unknown", "standard"))
        self.assertEqual(result["pid"], 1234)  # say which window was refused
        self.assertEqual((driver.captured, driver.sent), (0, 0))

    def test_a_drivability_refusal_is_retryable_after_the_executor_is_elevated(self):
        driver = FakeDriver(drivable=False, blocked_by="TARGET_NEEDS_ELEVATION")
        first = ui.run(PROFILE, "Analyze the error", event_id="EVT-ELEV-RETRY-1",
                       receipt_path=self.receipt, lock_path=self.lock,
                       driver=driver, poll_seconds=0.1)
        self.assertEqual(first["state"], "EXECUTOR_TOO_LOW")
        # Same event id: nothing was typed, so a retry cannot duplicate a message.
        driver.drivable = True
        second = ui.run(PROFILE, "Analyze the error", event_id="EVT-ELEV-RETRY-1",
                        receipt_path=self.receipt, lock_path=self.lock,
                        driver=driver, poll_seconds=0.1)
        self.assertEqual(second["state"], "AGENT_REPLY_VERIFIED")
        self.assertEqual(driver.sent, 1)

    def test_a_missing_verdict_is_not_a_refusal(self):
        # The check is Windows-only and needs a pid.  Absent evidence must never
        # become a new way to block delivery.
        self.assertEqual(ui.drivability_block({}), {})
        self.assertEqual(ui.drivability_block({"drivable": True}), {})
        self.assertEqual(ui.drivability_block({"status": "NO_WINDOW"}), {})
        self.assertEqual(ui.drivability_block(None), {})

    def test_the_refusal_carries_the_verdict_without_inventing_one(self):
        blocked = ui.drivability_block({"drivable": False,
                                        "target_elevation": "elevated",
                                        "executor_elevation": "standard",
                                        "blocked_by": "TARGET_NEEDS_ELEVATION"})
        self.assertEqual(blocked, {"drivable": False, "target_elevation": "elevated",
                                   "executor_elevation": "standard",
                                   "blocked_by": "TARGET_NEEDS_ELEVATION"})
        self.assertNotIn("blocked_by", ui.drivability_block(
            {"drivable": False, "target_elevation": "unknown", "executor_elevation": "standard"}))

    def test_busy_urgent_interject_requires_explicit_non_interrupting_action(self):
        profile = {**PROFILE, "allow_busy_interject": True}
        driver = FakeDriver(activity_state="busy", interject_ready=True)
        result = ui.run(profile, "Urgent note", event_id="EVT-URGENT-1",
                        receipt_path=self.receipt, lock_path=self.lock,
                        driver=driver, poll_seconds=0.1, urgent=True)
        self.assertEqual(result["state"], "AGENT_REPLY_VERIFIED")
        self.assertEqual(driver.sent, 1)

    def test_busy_urgent_with_stop_only_never_types(self):
        profile = {**PROFILE, "allow_busy_interject": True}
        driver = FakeDriver(activity_state="busy", interject_ready=False)
        result = ui.run(profile, "Urgent note", event_id="EVT-URGENT-STOP-1",
                        receipt_path=self.receipt, lock_path=self.lock,
                        driver=driver, poll_seconds=0.1, urgent=True)
        self.assertEqual(result["state"], "URGENT_INTERJECT_UNAVAILABLE")
        self.assertEqual(driver.sent, 0)

    def test_urgent_receipt_rejects_interruption(self):
        profile = {**PROFILE, "allow_busy_interject": True}
        driver = FakeDriver(activity_state="busy", interject_ready=True, interruption=True)
        result = ui.run(profile, "Urgent note", event_id="EVT-URGENT-INT-1",
                        receipt_path=self.receipt, lock_path=self.lock,
                        driver=driver, poll_seconds=0.1, urgent=True)
        self.assertEqual(result["state"], "URGENT_INTERJECT_UNCERTAIN")

    def test_powershell_driver_passes_enter_and_urgent_flags(self):
        driver = ui.PowerShellUiaDriver.__new__(ui.PowerShellUiaDriver)
        driver.pwsh = "pwsh.exe"
        captured = {}

        def fake_run(script, args, **kwargs):
            captured["args"] = args
            return {"status": "message-visible", "interject_action": "Interject",
                    "interruption": False}

        driver._run = fake_run
        profile = {**PROFILE, "send_mode": "enter", "transport": "uia",
                   "interject_hints": ["Interject"]}
        result = driver.deliver(profile, "EVT-PS-1", "EVT-PS-1", urgent=True)
        self.assertEqual(result["status"], "message-visible")
        self.assertIn("-SendMode", captured["args"])
        self.assertIn("enter", captured["args"])
        self.assertIn("-UrgentInterject", captured["args"])


if __name__ == "__main__":
    unittest.main()
