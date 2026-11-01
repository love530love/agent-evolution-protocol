"""Two products whose names nest must not share a window.

``Qoder`` is a substring of ``Qoder CN``, and the matcher's legacy fallback is a
process-name substring test against the window title.  Measured on this machine
before the fix, the ``qoder`` profile matched the ``Qoder CN`` window while
``qoder_cn`` correctly did not match ``Qoder`` -- an asymmetry that sends one
client's work into the other whenever only the CN client is running.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from agent_evolution_protocol.coordination import client_registry


QODER_EXE = r"D:\Programs\Qoder\.qoder-versions\0.5.1\Qoder.exe"
QODER_CN_EXE = r"D:\Programs\QoderCN\Qoder CN\.qoder-versions\0.4.11\Qoder CN.exe"


def _profiles(root):
    folder = root / "clients"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "qoder.json").write_text(json.dumps({
        "agent": "qoder", "process": "Qoder", "exe": QODER_EXE,
        "transports": ["uia"]}), encoding="utf-8")
    (folder / "qoder_cn.json").write_text(json.dumps({
        "agent": "qoder_cn", "process": "Qoder CN", "exe": QODER_CN_EXE,
        "transports": ["uia"]}), encoding="utf-8")


def _window(pid, title, exe):
    return {"hwnd": pid, "pid": pid, "title": title, "exe": exe}


class WindowIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        _profiles(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_identity_prefers_the_executable_and_says_so(self):
        profile = {"process": "Qoder", "exe": QODER_EXE}
        self.assertEqual(client_registry.identity(profile, _window(1, "Qoder", QODER_EXE)), "exe")
        self.assertEqual(client_registry.identity(profile, _window(1, "Qoder", "")), "process-substring")

    def test_the_legacy_matcher_still_matches_the_nested_title(self):
        # This is the behaviour that caused the wrong send; the test pins it so
        # the fix is understood as arbitration, not as a stricter substring.
        self.assertTrue(client_registry.matches(
            {"process": "Qoder"}, _window(1, "Qoder CN", "")))

    def test_another_profiles_executable_takes_the_window_away(self):
        found = [
            {"agent": "qoder", "exe": QODER_EXE,
             "windows": [_window(11, "Qoder CN", QODER_CN_EXE), _window(22, "Qoder", QODER_EXE)]},
            {"agent": "qoder_cn", "exe": QODER_CN_EXE,
             "windows": [_window(11, "Qoder CN", QODER_CN_EXE)]},
        ]
        rejected = client_registry._settle_by_executable(found)
        self.assertEqual([item["title"] for item in rejected], ["Qoder CN"])
        self.assertEqual(rejected[0]["owner"], "qoder_cn")
        self.assertEqual([hit["title"] for hit in found[0]["windows"]], ["Qoder"])
        self.assertEqual([hit["title"] for hit in found[1]["windows"]], ["Qoder CN"])

    def test_a_stale_profile_path_does_not_make_the_client_unreachable(self):
        # Version directories drift on every update.  If a path mismatch were a
        # filter, the client would become undiscoverable -- trading a wrong send
        # for no send at all, which is not obviously the better trade.
        found = [{"agent": "qoder", "exe": r"D:\Programs\Qoder\Qoder.exe",
                  "windows": [_window(22, "Qoder", QODER_EXE)]}]
        self.assertEqual(client_registry._settle_by_executable(found), [])
        self.assertEqual(len(found[0]["windows"]), 1)

    def test_discover_reports_the_identity_and_recomputes_uniqueness(self):
        windows = [_window(11, "Qoder CN", QODER_CN_EXE), _window(22, "Qoder", QODER_EXE)]
        with patch.object(client_registry, "visible_windows", return_value=windows):
            discovered = client_registry.discover(self.root, "qoder")
        row = discovered["clients"][0]
        self.assertEqual([hit["title"] for hit in row["windows"]], ["Qoder"])
        self.assertEqual(row["windows"][0]["identity"], "exe")
        self.assertTrue(row["unique"])
        # The rejection is reported even when the right window is also present:
        # a silent arbitration is indistinguishable from no arbitration.
        self.assertEqual([item["title"] for item in discovered["rejected"]], ["Qoder CN"])

    def test_discover_refuses_a_window_that_belongs_to_the_other_product(self):
        # Only the CN client is running.  Before arbitration, `qoder` claimed it
        # and `unique` was True -- one wrong window, described as unambiguous.
        windows = [_window(11, "Qoder CN", QODER_CN_EXE)]
        with patch.object(client_registry, "visible_windows", return_value=windows):
            discovered = client_registry.discover(self.root, "qoder")
        row = discovered["clients"][0]
        self.assertEqual(row["windows"], [])
        self.assertFalse(row["unique"])
        self.assertEqual(discovered["rejected"][0]["owner"], "qoder_cn")
        self.assertEqual(discovered["rejected"][0]["reason"],
                         "the executable belongs to another registered profile")

    def test_windows_without_a_readable_executable_still_discover(self):
        # `image_path` fails for a protected process; an empty path must mean
        # "not known", never "mismatch".
        windows = [{"hwnd": 1, "pid": 1, "title": "Qoder"}]
        with patch.object(client_registry, "visible_windows", return_value=windows):
            discovered = client_registry.discover(self.root, "qoder")
        self.assertEqual(len(discovered["clients"][0]["windows"]), 1)
        self.assertEqual(discovered["clients"][0]["windows"][0]["identity"], "process-substring")


if __name__ == "__main__":
    unittest.main()
