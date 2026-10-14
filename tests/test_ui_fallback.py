import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agent_evolution_protocol.coordination import ui_fallback as ui
from agent_evolution_protocol.coordination import wake_daemon


PROFILE = {"agent": "fake_agent", "process": "FakeAgent", "title": "Fake Agent",
           "exe": str((Path.cwd() / "FakeAgent.exe").resolve()), "session_marker": "Original task chat",
           "model_marker": "Model X", "transport": "uia", "reply_timeout_seconds": 5}


class FakeDriver:
    def __init__(self, *, model=True, session=True, input_found=True, no_window=False,
                 send_status="submitted-unconfirmed"):
        self.model = model
        self.session = session
        self.input_found = input_found
        self.no_window = no_window
        self.send_status = send_status
        self.sent = 0
        self.launched = 0
        self.selected = 0
        self.expected_ack = ""

    def observe(self, profile):
        if self.no_window:
            return {"status": "NO_WINDOW"}
        return {"status": "window-observed", "pid": 1234, "input_found": self.input_found,
                "input_ambiguous": False}

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

    def deliver(self, profile, message, event_id):
        self.sent += 1
        import re
        match = re.search(r"校验串 ([0-9A-F]+) 的倒序", message)
        assert match
        self.expected_ack = "AEP-ACK:" + match.group(1)[::-1]
        assert self.expected_ack not in message
        return {"status": self.send_status}


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


if __name__ == "__main__":
    unittest.main()
