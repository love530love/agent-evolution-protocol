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
                 interject_ready=False, interruption=False):
        self.model = model
        self.session = session
        self.input_found = input_found
        self.no_window = no_window
        self.send_status = send_status
        self.activity_state = activity_state
        self.interject_ready = interject_ready
        self.interruption = interruption
        self.sent = 0
        self.launched = 0
        self.selected = 0
        self.expected_ack = ""

    def observe(self, profile):
        if self.no_window:
            return {"status": "NO_WINDOW"}
        return {"status": "window-observed", "pid": 1234, "input_found": self.input_found,
                "input_ambiguous": False, "activity_state": self.activity_state,
                "interject_evidence": {"ready": self.interject_ready,
                                       "interrupt_only": False}}

    def capture(self, profile):
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
