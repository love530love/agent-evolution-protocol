"""The path a profile names has to be checkable at any integrity level.

``Process.MainModule`` throws for a higher-integrity process and
``Get-Process | Select Path`` returns empty, so a path check written that way is
skipped for exactly the clients that run elevated on purpose.  These tests pin
the API that does work and the honest reporting of "not checked".
"""
import os
import sys
import unittest
from unittest import mock

from agent_evolution_protocol.coordination import process_info


class PathCheckTests(unittest.TestCase):
    def test_an_empty_expectation_is_not_a_verdict(self):
        result = process_info.path_check("", 4)
        self.assertIsNone(result["match"])
        self.assertEqual(result["reason"], "no expected path configured")

    def test_a_missing_pid_is_not_a_verdict(self):
        result = process_info.path_check("C:/x/y.exe", None)
        self.assertIsNone(result["match"])
        self.assertEqual(result["reason"], "no pid to compare")

    def test_an_unreadable_image_is_not_a_verdict(self):
        with mock.patch.object(process_info, "available", return_value=True), \
                mock.patch.object(process_info, "image_path", return_value=""):
            result = process_info.path_check("C:/x/y.exe", 4)
        self.assertIsNone(result["match"])
        self.assertEqual(result["reason"], "the image path could not be read")

    def test_a_different_executable_is_reported_as_a_mismatch(self):
        with mock.patch.object(process_info, "available", return_value=True), \
                mock.patch.object(process_info, "image_path",
                                  return_value=r"D:\Apps\Qoder\0.5.1\Qoder.exe"):
            result = process_info.path_check(r"D:\Apps\Qoder\0.4.11\Qoder.exe", 1234)
        self.assertFalse(result["match"])
        self.assertIn("not the path", result["reason"])
        self.assertEqual(result["actual"], r"D:\Apps\Qoder\0.5.1\Qoder.exe")

    def test_case_and_separator_differences_are_not_drift(self):
        with mock.patch.object(process_info, "available", return_value=True), \
                mock.patch.object(process_info, "image_path",
                                  return_value=r"D:\Apps\Qoder\0.5.1\Qoder.exe"):
            result = process_info.path_check(r"d:/apps/qoder/0.5.1/Qoder.exe", 1234)
        self.assertTrue(result["match"])

    @unittest.skipUnless(sys.platform == "win32", "Windows process queries")
    def test_our_own_process_path_is_readable(self):
        path = process_info.image_path(os.getpid())
        self.assertTrue(path, "a process must be able to name its own executable")
        self.assertEqual(process_info.path_check(path, os.getpid())["match"], True)

    @unittest.skipUnless(sys.platform == "win32", "Windows process queries")
    def test_pid_zero_has_no_image_path(self):
        self.assertEqual(process_info.image_path(0), "")


if __name__ == "__main__":
    unittest.main()
