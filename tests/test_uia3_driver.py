"""The UIA3 driver, exercised against a fake accessibility tree.

These tests do not touch a real client: the point is the decision logic --
which control counts as a composer, what counts as proof of a write, and when
the driver must stand down and let the OCR adapter run.  A live test would
answer "does this build cooperate", which is a different question and one that
has to be re-asked on every launch (renderer accessibility is decided at
startup, and the same client answered 1090 elements on one launch and 9 on the
next).
"""

import unittest
from unittest.mock import patch

from agent_evolution_protocol.coordination import uia3


class FakeRect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom

    def width(self):
        return self.right - self.left

    def height(self):
        return self.bottom - self.top


class FakeInfo:
    def __init__(self, control_type="Pane", name="", enabled=True, process_id=4321,
                 handle=77, automation_id="", class_name=""):
        self.control_type = control_type
        self.name = name
        self.enabled = enabled
        self.process_id = process_id
        self.handle = handle
        self.automation_id = automation_id
        self.class_name = class_name


class FakeValue:
    def __init__(self, owner):
        self._owner = owner

    @property
    def CurrentValue(self):
        return self._owner.value

    def SetValue(self, text):
        if not self._owner.writable:
            raise RuntimeError("ValuePattern is read-only")
        self._owner.value = text


class FakeElement:
    def __init__(self, control_type="Pane", name="", enabled=True, focusable=True,
                 rect=None, value="", writable=True):
        self.element_info = FakeInfo(control_type=control_type, name=name, enabled=enabled)
        self._focusable = focusable
        self._rect = rect or FakeRect(0, 0, 100, 40)
        self.value = value
        self.writable = writable
        self.focused = 0
        self.keys = []
        self.clicks = 0
        self.iface_value = FakeValue(self)

    def rectangle(self):
        return self._rect

    def is_keyboard_focusable(self):
        return self._focusable

    def set_focus(self):
        self.focused += 1

    def type_keys(self, keys):
        self.keys.append(keys)
        if keys == "^a^v":
            self.value = self._clipboard_text
        elif keys == "{ENTER}":
            # A real client clears the editor when the message is submitted.
            self.value = ""

    def click_input(self):
        self.clicks += 1


class FakeWindow:
    def __init__(self, elements, title="Client"):
        self._elements = elements
        self._title = title
        self.element_info = FakeInfo(control_type="Window", name=title)

    def descendants(self):
        return list(self._elements)

    def window_text(self):
        return self._title

    def rectangle(self):
        return FakeRect(0, 0, 1200, 900)


def _profile(**extra):
    profile = {"agent": "client", "process": "Client", "title": "Client",
               "exe": "C:/nonexistent/Client.exe", "transport": "ocr",
               "session_marker": "Task chat", "model_marker": "Model",
               "reply_timeout_seconds": 5, "readback_timeout_seconds": 1}
    profile.update(extra)
    return profile


class ComposerSelectionTests(unittest.TestCase):
    def test_the_lowest_focusable_edit_is_the_composer(self):
        # A chat composer sits under the transcript; a URL bar or a search box
        # sits above it.  Picking the first editable control in the tree is how
        # an earlier adapter typed a dispatch into the address bar.
        search = FakeElement(control_type="Edit", name="search",
                             rect=FakeRect(0, 10, 400, 50))
        composer = FakeElement(control_type="Edit", name="composer",
                               rect=FakeRect(0, 800, 1200, 880))
        driver = uia3.Uia3Driver()
        chosen = driver.composer_candidates(FakeWindow([search, composer]))
        self.assertEqual([element.element_info.name for element in chosen],
                         ["search", "composer"])

    def test_disabled_and_unfocusable_controls_are_not_candidates(self):
        disabled = FakeElement(control_type="Edit", name="disabled", enabled=False,
                               rect=FakeRect(0, 800, 1200, 880))
        unfocusable = FakeElement(control_type="Edit", name="unfocusable", focusable=False,
                                  rect=FakeRect(0, 800, 1200, 880))
        pane = FakeElement(control_type="Pane", name="pane",
                           rect=FakeRect(0, 900, 1200, 950))
        driver = uia3.Uia3Driver()
        self.assertEqual(driver.composer_candidates(FakeWindow([disabled, unfocusable, pane])), [])


class ObserveTests(unittest.TestCase):
    def test_observe_reports_structure_and_busy_state(self):
        elements = [FakeElement(control_type="Text", name="Transcript"),
                    FakeElement(control_type="Button", name="停止生成"),
                    FakeElement(control_type="Edit", name="composer",
                                rect=FakeRect(0, 800, 1200, 880))]
        driver = uia3.Uia3Driver()
        with patch.object(driver, "find_window", return_value=(FakeWindow(elements), {"handle": 1})):
            report = driver.observe(_profile())
        self.assertEqual(report["client"], "uia3")
        self.assertEqual(report["element_count"], 3)
        self.assertEqual(report["input_candidate_count"], 1)
        self.assertTrue(report["input_found"])
        self.assertEqual(report["activity_state"], "busy")
        self.assertIn("停止生成", report["activity_evidence"]["hits"])

    def test_capture_reads_names_and_editor_values(self):
        elements = [FakeElement(control_type="Text", name="hello"),
                    FakeElement(control_type="Edit", name="composer", value="draft")]
        driver = uia3.Uia3Driver()
        with patch.object(driver, "find_window", return_value=(FakeWindow(elements), {})):
            text = driver.capture(_profile())
        self.assertIn("hello", text)
        self.assertIn("draft", text)


class DeliverTests(unittest.TestCase):
    def test_valuepattern_write_is_proven_by_reading_the_event_id_back(self):
        composer = FakeElement(control_type="Edit", name="composer",
                               rect=FakeRect(0, 800, 1200, 880))
        window = FakeWindow([composer])
        driver = uia3.Uia3Driver()

        def _capture(_profile):
            # After the send, the message is in the transcript and the editor
            # is empty again.
            return "conversation EVT-ABCDEFG" if composer.keys else ""

        with patch.object(driver, "find_window", return_value=(window, {})), \
                patch.object(driver, "capture", side_effect=_capture), \
                patch.object(uia3.time, "sleep"):
            result = driver.deliver(_profile(), "body EVT-ABCDEFG", "EVT-ABCDEFG")
        self.assertEqual(result["status"], "message-visible")
        self.assertTrue(result["task_delivered"])
        self.assertEqual(result["write_method"], "uia3-valuepattern")
        self.assertEqual(composer.keys, ["{ENTER}"])

    def test_a_read_only_editor_falls_back_to_the_clipboard(self):
        composer = FakeElement(control_type="Edit", name="composer", writable=False,
                               rect=FakeRect(0, 800, 1200, 880))
        window = FakeWindow([composer])
        driver = uia3.Uia3Driver()
        with patch.object(driver, "find_window", return_value=(window, {})), \
                patch.object(driver, "_set_clipboard") as clipboard, \
                patch.object(driver, "capture", return_value="body EVT-ABCDEFG"), \
                patch.object(uia3.time, "sleep"):
            composer._clipboard_text = "body EVT-ABCDEFG"
            result = driver.deliver(_profile(), "body EVT-ABCDEFG", "EVT-ABCDEFG")
        clipboard.assert_called_once()
        self.assertEqual(result["write_method"], "uia3-clipboard-paste")
        self.assertEqual(result["status"], "message-visible")

    def test_no_editable_control_is_reported_instead_of_clicked(self):
        driver = uia3.Uia3Driver()
        with patch.object(driver, "find_window", return_value=(FakeWindow([]), {})):
            result = driver.deliver(_profile(), "body", "EVT-ABCDEFG")
        self.assertEqual(result["status"], "NO_INPUT_CONTROL")
        self.assertFalse(result["task_delivered"])


class FallbackDriver:
    """Minimal stand-in for the OCR adapter."""

    def __init__(self):
        self.calls = []

    def observe(self, profile):
        self.calls.append("observe")
        return {"status": "window-observed", "activity_state": "ready", "client": "powershell"}

    def capture(self, profile):
        self.calls.append("capture")
        return "ocr transcript"

    def select_session(self, profile):
        self.calls.append("select_session")
        return {"status": "session-selection-attempted"}

    def launch(self, profile):
        self.calls.append("launch")

    def deliver(self, profile, message, event_id, *, urgent=False, composer_ratios=None):
        self.calls.append("deliver")
        return {"status": "message-visible", "task_delivered": True, "client": "powershell"}


class AutoDriverTests(unittest.TestCase):
    def test_no_tree_means_the_ocr_adapter_does_everything(self):
        # WorkBuddy exposes eight panes and a title bar through both UIA
        # clients, so this is the live shape for that client, not a hypothetical.
        fallback = FallbackDriver()
        driver = uia3.AutoDriver(fallback=fallback)
        with patch.object(driver.uia3, "has_tree", return_value=False):
            profile = _profile(prefer_uia3=True)
            self.assertEqual(driver.capture(profile), "ocr transcript")
            self.assertEqual(driver.deliver(profile, "m", "EVT-1")["client"], "powershell")
        self.assertEqual(fallback.calls, ["capture", "deliver"])

    def test_rich_tree_uses_the_full_tree_client(self):
        fallback = FallbackDriver()
        driver = uia3.AutoDriver(fallback=fallback)
        composer = FakeElement(control_type="Edit", name="composer",
                               rect=FakeRect(0, 800, 1200, 880))
        window = FakeWindow([composer])
        with patch.object(driver.uia3, "has_tree", return_value=True), \
                patch.object(driver.uia3, "find_window", return_value=(window, {})), \
                patch.object(driver.uia3, "capture", return_value="tree transcript"):
            self.assertEqual(driver.capture(_profile(prefer_uia3=True)), "tree transcript")
        self.assertNotIn("capture", fallback.calls)

    def test_uia3_is_opt_in_per_profile(self):
        fallback = FallbackDriver()
        driver = uia3.AutoDriver(fallback=fallback)
        with patch.object(driver.uia3, "has_tree", return_value=True) as has_tree:
            driver.capture(_profile())
        has_tree.assert_not_called()
        self.assertEqual(fallback.calls, ["capture"])

    def test_urgent_interject_stays_on_the_adapter_that_can_name_the_control(self):
        fallback = FallbackDriver()
        driver = uia3.AutoDriver(fallback=fallback)
        with patch.object(driver.uia3, "has_tree", return_value=True):
            result = driver.deliver(_profile(prefer_uia3=True), "m", "EVT-1", urgent=True)
        self.assertEqual(result["client"], "powershell")

    def test_busy_detection_is_overlaid_from_pixels(self):
        # The busy indicator is an animation with no accessible name; if the
        # tree decided readiness on its own, a dispatch would type over a live
        # generation run.
        fallback = FallbackDriver()
        driver = uia3.AutoDriver(fallback=fallback)
        with patch.object(driver.uia3, "has_tree", return_value=True), \
                patch.object(driver.uia3, "observe",
                             return_value={"status": "window-observed", "activity_state": "unknown",
                                           "activity_evidence": {"source": "uia3-names"}}):
            report = driver.observe(_profile(prefer_uia3=True, activity_ocr=True))
        self.assertEqual(report["activity_state"], "ready")
        self.assertEqual(fallback.calls, ["observe"])


if __name__ == "__main__":
    unittest.main()
