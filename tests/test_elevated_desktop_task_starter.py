"""Static contract for the exact-order elevated task starter."""

from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[1]


class ElevatedDesktopTaskStarterTests(unittest.TestCase):
    def test_starter_is_ascii_gated_and_exact_order_only(self):
        path = REPO / "scripts" / "start-elevated-desktop-order-task.ps1"
        raw = path.read_bytes()
        self.assertTrue(all(byte < 128 for byte in raw))
        text = raw.decode("ascii")
        for token in (
            "[string]$OrderId", "[string]$EventId", "[switch]$Apply",
            "ORDER_ID_MISMATCH", "EVENT_ID_MISMATCH", "ORDER_NOT_DEPOSITED",
            "LAUNCH_FORBIDDEN", "ORDER_EXPIRED", "RECEIPT_ALREADY_EXISTS",
            "TASK_NOT_REGISTERED", "TASK_RUNLEVEL_INVALID", "TASK_LOGON_TYPE_INVALID",
            "Get-ScheduledTask", "schtasks.exe /Query", "InteractiveToken",
            "HighestAvailable", "$PSVersionTable.PSEdition", "Start-ScheduledTask", "TASK_START_REQUESTED",
            "delivery_success = $false", "task_delivered = $false", "agent_accepted = $false",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)
        start = text.index("Start-ScheduledTask")
        self.assertGreater(start, text.index("if ($Apply)"))
        self.assertNotIn("Register-ScheduledTask", text)
        self.assertNotIn("commander.dispatch", text)


if __name__ == "__main__":
    unittest.main()
