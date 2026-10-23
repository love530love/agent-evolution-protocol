import json
import errno
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_evolution_protocol.coordination import campaign_cycle as cycle


class AtomicTests(unittest.TestCase):
    def test_windows_snapshot_reads_retry_bounded_sharing_denial(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            for code in (None, 5, 32, 33):
                with self.subTest(winerror=code):
                    error = PermissionError(errno.EACCES, "sharing denied")
                    if code is not None:
                        error.winerror = code
                    with patch.object(cycle, "os", SimpleNamespace(name="nt")), \
                            patch.object(Path, "read_text", side_effect=[error, error, '{"latest": true}']) as read, \
                            patch.object(cycle.time, "sleep") as sleep:
                        self.assertEqual(cycle._read_json_snapshot(path), {"latest": True})
                        self.assertEqual(read.call_count, 3)
                        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.02, 0.04])
                    self.assertEqual(list(Path(temp).iterdir()), [])

    def test_snapshot_read_denial_and_invalid_json_are_not_hidden(self):
        path = Path("does-not-need-to-exist.json")
        for platform, code, attempts in (("nt", None, 4), ("nt", 5, 4), ("nt", 87, 1), ("posix", None, 1)):
            with self.subTest(platform=platform, winerror=code):
                error = PermissionError(errno.EACCES, "denied")
                if code is not None:
                    error.winerror = code
                with patch.object(cycle, "os", SimpleNamespace(name=platform)), \
                        patch.object(Path, "read_text", side_effect=error) as read, \
                        patch.object(cycle.time, "sleep") as sleep:
                    with self.assertRaises(PermissionError):
                        cycle._read_json_snapshot(path)
                    self.assertEqual(read.call_count, attempts)
                    self.assertEqual(sleep.call_count, attempts - 1)
        with patch.object(Path, "read_text", return_value="{") as read, patch.object(cycle.time, "sleep") as sleep:
            with self.assertRaises(json.JSONDecodeError):
                cycle._read_json_snapshot(path)
            read.assert_called_once()
            sleep.assert_not_called()

    def test_transient_windows_denial_retries_same_snapshot(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            cycle._atomic(path, {"old": True})
            replace = cycle.os.replace
            error = PermissionError("sharing")
            error.winerror = 5
            with patch.object(cycle.os, "replace", side_effect=[error, error, None]) as mocked:
                with patch.object(cycle.time, "sleep") as sleep:
                    # Last attempt performs the real replacement.
                    def attempt(source, target):
                        if mocked.call_count < 3:
                            raise error
                        replace(source, target)
                    mocked.side_effect = attempt
                    cycle._atomic(path, {"new": True})
                    self.assertEqual(mocked.call_count, 3)
                    self.assertEqual(sleep.call_count, 2)
            self.assertEqual(json.loads(path.read_text()), {"new": True})
            self.assertEqual(list(Path(temp).glob("*.tmp")), [])

    def test_persistent_denial_preserves_original_and_stops(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            cycle._atomic(path, {"old": True})
            for code, attempts in ((32, 4), (None, 1)):
                error = PermissionError("denied")
                error.winerror = code
                with patch.object(cycle.os, "replace", side_effect=error) as replace:
                    with patch.object(cycle.time, "sleep"):
                        with self.assertRaises(PermissionError):
                            cycle._atomic(path, {"new": True})
                    self.assertEqual(replace.call_count, attempts)
                self.assertEqual(json.loads(path.read_text()), {"old": True})
                self.assertEqual(list(Path(temp).glob("*.tmp")), [])
