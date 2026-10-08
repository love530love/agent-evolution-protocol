import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from agent_evolution_protocol import cli


class CoordinationCliTest(unittest.TestCase):
    def test_custom_agent_is_visible_after_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with redirect_stdout(StringIO()):
                cli.coord_claim(SimpleNamespace(coord_root=root, agent="custom_agent", task="docs-intro", paths=[]))
            with redirect_stdout(StringIO()) as output:
                cli.coord_status(SimpleNamespace(coord_root=root))
            self.assertIn("custom_agent", json.loads(output.getvalue())["sessions"])
            self.assertEqual("custom_agent", cli.infer_target_agent("通知 custom_agent", "codex", cli.known_agents(root)))

    def test_bootstrap_send_digest_wake_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = SimpleNamespace(coord_root=root)
            cli.coord_bootstrap(args)
            self.assertTrue((root / "sessions" / "codex.json").exists())

            cli.coord_session_set(SimpleNamespace(
                coord_root=root,
                agent="codex",
                mode="sticky",
                thread_id="current",
                new_session_policy="forbid-by-default",
                handoff_style="digest-first",
                notes="test",
            ))
            state = json.loads((root / "sessions" / "codex.json").read_text(encoding="utf-8"))
            self.assertEqual("current", state["thread_id"])

            cli.coord_send(SimpleNamespace(
                coord_root=root,
                sender="codex",
                to="qoder",
                topic="T123",
                kind="update",
                text="hello",
                text_file=None,
            ))
            self.assertEqual(1, len(cli.unread_messages(root, "qoder")))

            cli.coord_wake(SimpleNamespace(
                coord_root=root,
                sender="codex",
                to="qoder",
                task="T123",
                reason="bounded test",
                budget="bounded",
                entrypoint="",
                requires_user_auth=False,
                dry_run=True,
                allow_new_session=False,
                claim_timeout_seconds=600,
            ))
            self.assertEqual(1, len(list((root / "wake_queue").glob("*.json"))))

            cli.coord_archive_stale(SimpleNamespace(
                coord_root=root,
                all=True,
                older_than_hours=24,
                archive_name="test-archive",
            ))
            self.assertEqual(0, len(list((root / "wake_queue").glob("*.json"))))
            self.assertTrue((root / "wake_done" / "test-archive" / "ARCHIVE_DIGEST.json").exists())

    def test_new_session_policy_defaults_to_forbid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cli.coord_bootstrap(SimpleNamespace(coord_root=root))
            session = cli.session_state(root, "hermes_desktop")
            self.assertEqual("sticky", session["mode"])
            self.assertEqual("forbid-by-default", session["new_session_policy"])
            self.assertEqual("digest-first", session["handoff_style"])

    def test_task_state_and_claim_lifecycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cli.coord_bootstrap(SimpleNamespace(coord_root=root))
            cli.coord_claim(SimpleNamespace(coord_root=root, agent="codex", task="T125", paths=["src/x.py"]))
            owner = json.loads((root / "claims" / "T125" / "owner.json").read_text(encoding="utf-8"))
            self.assertEqual("codex", owner["agent"])

            with self.assertRaises(cli.ProtocolError):
                cli.coord_release(SimpleNamespace(coord_root=root, agent="qoder", task="T125"))

            cli.coord_task_state(SimpleNamespace(
                coord_root=root,
                agent="codex",
                task="T125",
                phase="working",
                goal="bounded investigation",
                evidence=["digest"],
                blocked_reason=None,
                next_action="run falsification",
                wake_phrase=None,
                human_required=False,
                stale_after_hours=6,
            ))
            state = json.loads((root / "task_states" / "T125.json").read_text(encoding="utf-8"))
            self.assertEqual("working", state["phase"])
            self.assertEqual("@wake codex", state["wake_phrase"])

            cli.coord_release(SimpleNamespace(coord_root=root, agent="codex", task="T125"))
            self.assertFalse((root / "claims" / "T125" / "owner.json").exists())

    def test_onboarding_contract_and_guided_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cli.coord_bootstrap(SimpleNamespace(coord_root=root))

            out = StringIO()
            with redirect_stdout(out):
                cli.coord_onboarding(SimpleNamespace(coord_root=root, kernel_root=root / "kernel", agent="codex"))
            payload = json.loads(out.getvalue())
            self.assertIn("first_turn_contract", payload)
            self.assertIn("coord-guided-retry", " ".join(payload["rules"]))

            guidance = cli.classify_retry("timeout after submit", "submit upload", "high")
            self.assertEqual("uncertain-outcome", guidance["category"])
            self.assertFalse(guidance["retry_allowed"])
            self.assertEqual("UNKNOWN", guidance["kernel_state"])

            guidance = cli.classify_retry("element is obscured", "click dropdown", "low")
            self.assertEqual("stale-observation", guidance["category"])
            self.assertTrue(guidance["retry_allowed"])


if __name__ == "__main__":
    unittest.main()
