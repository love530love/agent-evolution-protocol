"""The screen-reader flag is a machine-wide switch; test it without flipping it.

Toggling SPI_SETSCREENREADER in a test would change how every application on
the developer's desktop behaves, so these tests only read state and verify the
command is wired.  The one write is a no-op round-trip: set the flag to the
value it already has.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

from agent_evolution_protocol import cli
from agent_evolution_protocol.coordination import a11y, ui_fallback


class PlatformTests(unittest.TestCase):
    def test_availability_matches_the_platform(self):
        self.assertEqual(a11y.available(), sys.platform == "win32")

    def test_calls_fail_loudly_off_windows(self):
        if sys.platform == "win32":
            self.skipTest("this assertion is about the non-Windows path")
        self.assertFalse(a11y.available())
        with self.assertRaises(RuntimeError):
            a11y.status()
        # Elevation is reported rather than raised: callers treat "unknown" as
        # "cannot drive this window", which is the honest answer.
        self.assertEqual(a11y.elevation(os.getpid()), "unknown")
        self.assertEqual(a11y.check()["available"], False)


@unittest.skipUnless(sys.platform == "win32", "Windows accessibility APIs")
class WindowsTests(unittest.TestCase):
    def test_status_is_a_bool(self):
        self.assertIn(a11y.status(), (True, False))

    def test_self_elevation_is_reported(self):
        self.assertIn(a11y.elevation(os.getpid()), ("standard", "elevated"))

    def test_setting_the_current_value_is_a_no_op_round_trip(self):
        before = a11y.status()
        self.assertEqual(a11y.set_enabled(before), before)

    def test_check_reports_what_is_needed_to_drive_a_window(self):
        report = a11y.check(os.getpid())
        self.assertTrue(report["available"])
        self.assertIn("screen_reader", report)
        self.assertEqual(report["target_elevation"], a11y.elevation(os.getpid()))
        # A process can always be driven by itself, so this must be true.
        self.assertTrue(report["drivable"])

    def test_an_unopenable_process_is_not_drivable(self):
        # Pid 0 cannot be opened, which stands in for the elevated-client case:
        # the answer must be "unknown", never a silent yes.
        self.assertEqual(a11y.elevation(0), "unknown")
        self.assertFalse(a11y.check(0)["drivable"])


class AnnotationTests(unittest.TestCase):
    """The driver must say *why* a window cannot be driven, not just fail."""

    def test_reports_are_left_alone_off_windows(self):
        if sys.platform == "win32":
            self.skipTest("this assertion is about the non-Windows path")
        report = {"pid": 4}
        self.assertEqual(ui_fallback.annotate_elevation(report), {"pid": 4})

    def test_a_report_without_a_pid_is_untouched(self):
        report = {"status": "NO_WINDOW"}
        self.assertEqual(ui_fallback.annotate_elevation(report), {"status": "NO_WINDOW"})

    @unittest.skipUnless(sys.platform == "win32", "Windows accessibility APIs")
    def test_an_unreadable_process_is_marked_undrivable(self):
        # Pid 4 is System: opening its token always fails, which is the same
        # answer an elevated client gives.
        report = ui_fallback.annotate_elevation({"pid": 4})
        self.assertEqual(report["target_elevation"], "unknown")
        self.assertFalse(report["drivable"])
        self.assertEqual(report["blocked_by"], "TARGET_NEEDS_ELEVATION")
        self.assertIn("elevated", report["hint"])

    @unittest.skipUnless(sys.platform == "win32", "Windows accessibility APIs")
    def test_our_own_process_is_drivable(self):
        report = ui_fallback.annotate_elevation({"pid": os.getpid()})
        self.assertTrue(report["drivable"])
        self.assertNotIn("blocked_by", report)

    @unittest.skipUnless(sys.platform == "win32", "Windows accessibility APIs")
    def test_both_sides_are_reported(self):
        # A bare "drivable: false" invites the wrong fix; the caller needs to
        # see which side is the high one.
        report = ui_fallback.annotate_elevation({"pid": os.getpid()})
        self.assertIn(report["target_elevation"], ("standard", "elevated"))
        self.assertIn(report["executor_elevation"], ("standard", "elevated"))

    def test_the_comparison_is_what_decides(self):
        # No Windows needed: replace the two probes with a standard executor
        # facing an elevated target and assert the report refuses.
        with mock.patch.object(a11y, "available", return_value=True), \
                mock.patch.object(a11y, "elevation", return_value="elevated"), \
                mock.patch.object(a11y, "self_elevation", return_value="standard"):
            report = ui_fallback.annotate_elevation({"pid": 4})
        self.assertFalse(report["drivable"])
        self.assertEqual(report["blocked_by"], "TARGET_NEEDS_ELEVATION")
        self.assertIn("standard", report["hint"])

    def test_a_standard_target_is_drivable_from_a_standard_executor(self):
        with mock.patch.object(a11y, "available", return_value=True), \
                mock.patch.object(a11y, "elevation", return_value="standard"), \
                mock.patch.object(a11y, "self_elevation", return_value="standard"):
            report = ui_fallback.annotate_elevation({"pid": 4})
        self.assertTrue(report["drivable"])
        self.assertNotIn("blocked_by", report)


class DrivabilityTests(unittest.TestCase):
    """UIPI is one-way, so the answer depends on both sides, not just the target.

    The defect this pins: ``drivable`` used to be "the target's token could be
    read".  That reads correctly only because Windows refuses the token of a
    higher-integrity process -- i.e. the comparison was being performed by the
    failure mode instead of by the code.  Any case where the token *is* readable
    and the executor is still the lower side would have been reported as a
    green light.
    """

    def test_equal_levels_are_drivable(self):
        for level in ("standard", "elevated"):
            self.assertEqual(a11y.drivability(level, level), (True, None))

    def test_a_lower_target_is_drivable(self):
        self.assertEqual(a11y.drivability("standard", "elevated"), (True, None))

    def test_a_higher_target_is_not(self):
        self.assertEqual(a11y.drivability("elevated", "standard"),
                         (False, "TARGET_NEEDS_ELEVATION"))

    def test_an_unreadable_target_needs_the_same_action_as_a_higher_one(self):
        # Windows withholds the token only when the target outranks us, so the
        # operator advice is identical and the code must match.
        self.assertEqual(a11y.drivability("unknown", "standard"),
                         (False, "TARGET_NEEDS_ELEVATION"))
        self.assertEqual(a11y.drivability("unknown", "elevated"),
                         (False, "TARGET_NEEDS_ELEVATION"))

    def test_an_unreadable_executor_is_its_own_reason(self):
        self.assertEqual(a11y.drivability("standard", "unknown"),
                         (False, "EXECUTOR_INTEGRITY_UNKNOWN"))


class CommandTests(unittest.TestCase):
    def test_the_command_is_registered(self):
        parsed = cli.build_parser().parse_args(["coord-a11y", "status", "--pid", "4"])
        self.assertEqual(parsed.action, "status")
        self.assertEqual(parsed.pid, 4)
        self.assertFalse(parsed.persist)


if __name__ == "__main__":
    unittest.main()
