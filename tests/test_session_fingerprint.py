"""The session's identity, not the window's.

A marker check asks "is this string on screen", which the sidebar also answers.
A fingerprint asks "is this the same conversation I sent to last time", and the
difference is what stops a message landing in the right client's wrong session.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_evolution_protocol.coordination import session_fingerprint


PROFILE = {"session_marker": "赛事夺冠执行", "model_marker": "DeepSeek-Flash",
           "exe": r"D:\Programs\Qoder\.qoder-versions\0.5.1\Qoder.exe"}
WINDOW = {"pid": 4242, "title": "Qoder",
          "exe": r"D:\Programs\Qoder\.qoder-versions\0.5.1\Qoder.exe"}


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def _observe(self, **changes):
        window = {**WINDOW, **{k: v for k, v in changes.items() if k in WINDOW}}
        profile = {**PROFILE, **{k: v for k, v in changes.items() if k in PROFILE}}
        return session_fingerprint.observe(agent="qoder", window=window, profile=profile)

    def test_a_restart_is_the_same_session(self):
        # A fingerprint that changes when the process restarts is one operators
        # learn to re-record without reading, which is the same as not having it.
        before = self._observe()
        after = self._observe(pid=9999)
        self.assertNotEqual(before["pid"], after["pid"])
        self.assertEqual(before["fingerprint"], after["fingerprint"])
        self.assertTrue(session_fingerprint.compare(before, after)["match"])

    def test_a_different_install_is_a_different_instance(self):
        observed = self._observe(exe=r"D:\Programs\QoderCN\Qoder CN\Qoder CN.exe")
        result = session_fingerprint.compare(self._observe(), observed)
        self.assertFalse(result["match"])
        self.assertEqual(result["diff"], ["exe"])

    def test_a_different_conversation_is_refused(self):
        observed = self._observe(session_marker="了解虚拟环境与加入协作区")
        result = session_fingerprint.compare(self._observe(), observed)
        self.assertFalse(result["match"])
        self.assertEqual(result["diff"], ["session_marker"])

    def test_a_model_switch_is_refused(self):
        observed = self._observe(model_marker="Qwen3")
        result = session_fingerprint.compare(self._observe(), observed)
        self.assertFalse(result["match"])
        self.assertEqual(result["diff"], ["model_marker"])

    def test_path_case_and_separators_do_not_change_the_identity(self):
        observed = self._observe(exe="d:/programs/qoder/.qoder-versions/0.5.1/qoder.exe")
        self.assertTrue(session_fingerprint.compare(self._observe(), observed)["match"])

    def test_nothing_recorded_is_unknown_not_a_match(self):
        result = session_fingerprint.compare(None, self._observe())
        self.assertIsNone(result["match"])
        self.assertIn("no fingerprint recorded", result["reason"])

    def test_record_and_read_round_trip(self):
        observed = self._observe()
        session_fingerprint.record(self.root, "qoder", observed)
        stored = session_fingerprint.read(self.root, "qoder")
        self.assertEqual(stored["fingerprint"], observed["fingerprint"])
        self.assertTrue(session_fingerprint.compare(stored, observed)["match"])
        # A later restart still matches what was recorded.
        self.assertTrue(session_fingerprint.compare(stored, self._observe(pid=1))["match"])

    def test_an_unreadable_record_is_treated_as_absent(self):
        target = session_fingerprint.path(self.root, "qoder")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{not json", encoding="utf-8")
        self.assertIsNone(session_fingerprint.read(self.root, "qoder"))

    def test_the_digest_is_reproducible_across_field_order(self):
        row = {"agent": "qoder", "exe": "x", "title": "Qoder",
               "session_marker": "s", "model_marker": "m"}
        shuffled = {"model_marker": "m", "session_marker": "s", "title": "Qoder",
                    "exe": "x", "agent": "qoder"}
        self.assertEqual(session_fingerprint.compute(row),
                         session_fingerprint.compute(shuffled))


if __name__ == "__main__":
    unittest.main()
