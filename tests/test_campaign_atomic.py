import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agent_evolution_protocol.coordination import campaign_cycle as cycle


class AtomicTests(unittest.TestCase):
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
