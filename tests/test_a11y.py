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

from agent_evolution_protocol import cli
from agent_evolution_protocol.coordination import a11y


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


class CommandTests(unittest.TestCase):
    def test_the_command_is_registered(self):
        parsed = cli.build_parser().parse_args(["coord-a11y", "status", "--pid", "4"])
        self.assertEqual(parsed.action, "status")
        self.assertEqual(parsed.pid, 4)
        self.assertFalse(parsed.persist)


if __name__ == "__main__":
    unittest.main()
