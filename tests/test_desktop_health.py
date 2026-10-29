from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_evolution_protocol.coordination import desktop_health


class DesktopHealthTests(unittest.TestCase):
    def test_circuit_opens_after_repeated_timeouts_and_recovers(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            first = desktop_health.record(root, component="native-window-enumeration", status="TIMEOUT")
            second = desktop_health.record(root, component="native-window-enumeration", status="TIMEOUT")
            self.assertEqual("PROBING", first["circuit"])
            self.assertEqual("DEGRADED", second["circuit"])
            recovered = desktop_health.record(root, component="native-window-enumeration", status="READY")
            self.assertEqual("READY", recovered["circuit"])
            self.assertEqual(0, recovered["consecutive_failures"])


if __name__ == "__main__":
    unittest.main()
