"""workbuddy / workbuddy3 / workbuddy_ai / workbuddyai are distinct agents.

Regression for the GitHub Copilot receipt confusion: it could only discover
``workbuddy`` because both hard-coded rosters omitted ``workbuddyai``.  The fix
must not turn into a prefix alias, or a wrong recipient would be silently
accepted instead of failing loudly.
"""
from argparse import Namespace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from agent_evolution_protocol import cli
from agent_evolution_protocol.coordination import agent_identity, bridge, client_registry


class IdentityTests(unittest.TestCase):
    def test_workbuddy_family_names_are_distinct_and_have_no_aliases(self):
        self.assertEqual(agent_identity.SELF, "workbuddyai")
        self.assertEqual(agent_identity.ALIASES, {})
        for left in agent_identity.WORKBUDDY_IDENTITIES:
            for right in agent_identity.WORKBUDDY_IDENTITIES:
                if left != right:
                    self.assertFalse(agent_identity.is_same(left, right))
        card = agent_identity.describe("workbuddy")
        self.assertIn("workbuddyai", card["confusable_with"])
        self.assertEqual(card["aliases_applied"], [])
        self.assertEqual(card["mailbox"], "messages/workbuddy")

    def test_both_rosters_share_one_source(self):
        expected = agent_identity.LEGACY_BRIDGE_AGENTS
        self.assertEqual(cli.AGENTS, expected)
        self.assertEqual(bridge.AGENTS, expected)
        for name in agent_identity.WORKBUDDY_IDENTITIES:
            self.assertIn(name, cli.AGENTS)
            self.assertIn(name, bridge.AGENTS)

    def test_legacy_bridge_accepts_the_exact_identity_only(self):
        self.assertEqual(bridge.agent("workbuddyai"), "workbuddyai")
        self.assertEqual(bridge.agent("workbuddy_ai"), "workbuddy_ai")
        with self.assertRaises(ValueError):
            bridge.agent("workbuddyai-typo")

    def test_coord_send_to_workbuddyai_uses_its_own_mailbox(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            args = Namespace(
                coord_root=str(root), sender="github_copilot", to="workbuddyai",
                topic="AEP-IDENTITY-TEST", kind="result",
                text="receipt for the exact workbuddyai mailbox",
                text_file=None, dispatch=False, mailbox_only=False,
                allow_launch=False,
            )
            cli.coord_send(args)
            target = sorted((root / "messages" / "workbuddyai").glob("*.json"))
            self.assertEqual(len(target), 1)
            msg = json.loads(target[0].read_text(encoding="utf-8"))
            self.assertEqual(msg["to"], "workbuddyai")
            self.assertEqual(msg["sender"], "github_copilot")
            self.assertFalse((root / "messages" / "workbuddy").exists())


class RegistryTests(unittest.TestCase):
    WINDOWS = [
        {"title": "WorkBuddy", "pid": 1, "hwnd": 1, "exe": r"D:\Programs\WorkBuddy\WorkBuddy.exe"},
        {"title": "WorkBuddy AI", "pid": 2, "hwnd": 2, "exe": r"D:\Programs\WorkBuddyAI\WorkBuddyAI.exe"},
    ]

    def test_identity_only_profile_never_matches_a_window(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            row = client_registry.profile(root, "workbuddyai")
            self.assertTrue(row["identity_only"])
            for window in self.WINDOWS:
                self.assertFalse(client_registry.matches(row, window))

    def test_registration_cannot_turn_the_identity_into_a_target(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "clients").mkdir()
            (root / "clients" / "workbuddyai.json").write_text(json.dumps({
                "agent": "workbuddyai", "display": "WorkBuddy AI",
                "process": "WorkBuddyAI", "window_titles": ["WorkBuddy AI"],
                "identity_only": False, "transports": ["uia"],
            }), encoding="utf-8")
            row = client_registry.profile(root, "workbuddyai")
            self.assertTrue(row["identity_only"], "a typo in clients/ must not re-enable targeting")
            self.assertEqual(row["distinct_identity"], "workbuddyai")
            self.assertFalse(client_registry.matches(row, self.WINDOWS[1]))

    def test_discover_offers_no_window_for_workbuddyai(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            with mock.patch.object(client_registry, "visible_windows", return_value=list(self.WINDOWS)):
                found = client_registry.discover(root, "workbuddyai")
            row = next(item for item in found["clients"] if item["agent"] == "workbuddyai")
            self.assertTrue(row["identity_only"])
            self.assertEqual(row["windows"], [])
            self.assertFalse(row["unique"])

    def test_runtime_registration_if_present_is_identity_only(self):
        path = Path(__file__).resolve().parent.parent / "coordination" / "clients" / "workbuddyai.json"
        if not path.is_file():
            self.skipTest("runtime client registration is not present in this checkout")
        row = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(row["agent"], "workbuddyai")
        self.assertTrue(row.get("identity_only"))
        self.assertEqual(row.get("window_titles"), [])
        self.assertEqual(row.get("exe"), "")


if __name__ == "__main__":
    unittest.main()
