"""A launcher is not proof that an agent received or accepted a task."""

from pathlib import Path
import unittest


ADAPTERS = Path(__file__).resolve().parents[1] / "src" / "agent_evolution_protocol" / "coordination" / "wake_adapters"


class LaunchOnlyWakeReceiptTests(unittest.TestCase):
    def test_launch_only_adapters_do_not_claim_delivery(self):
        for name in ("autoclaw", "codebuddy", "github_copilot", "hermes_desktop"):
            with self.subTest(agent=name):
                source = (ADAPTERS / f"{name}.ps1").read_text(encoding="utf-8")
                self.assertIn("task_delivered = $false", source)
                self.assertIn("agent_accepted = $false", source)
                self.assertNotIn("status = 'wake-dispatched'", source)

    def test_sticky_session_never_blindly_starts_a_new_client(self):
        for name in ("autoclaw", "codebuddy", "github_copilot", "hermes_desktop"):
            with self.subTest(agent=name):
                source = (ADAPTERS / f"{name}.ps1").read_text(encoding="utf-8")
                self.assertIn("allow_new_session", source)


if __name__ == "__main__":
    unittest.main()
