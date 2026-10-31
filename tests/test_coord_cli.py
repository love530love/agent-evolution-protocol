import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from agent_evolution_protocol import cli
from agent_evolution_protocol.coordination import claim_conflicts


class CoordinationCliTest(unittest.TestCase):
    def test_parallel_candidates_do_not_steal_task_lead(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with redirect_stdout(StringIO()):
                cli.coord_claim(SimpleNamespace(coord_root=root, agent="codex", task="T132", paths=[]))
                cli.coord_claim(SimpleNamespace(coord_root=root, agent="workbuddy", task="T132-WB-R1", paths=["wb/t132"] ))
                cli.coord_claim(SimpleNamespace(coord_root=root, agent="qoder", task="T132-QODER-R1", paths=["qoder/t132"] ))
                cli.coord_task_state(SimpleNamespace(coord_root=root, agent="qoder", task="T132-QODER-R1",
                    phase="working", goal="cross-platform branch", evidence=[], blocked_reason="", next_action="compile",
                    wake_phrase="", human_required=False, stale_after_hours=12))
            self.assertEqual("codex", json.loads((root / "claims" / "T132" / "owner.json").read_text(encoding="utf-8"))["agent"])
            self.assertEqual("qoder", json.loads((root / "task_states" / "T132-QODER-R1.json").read_text(encoding="utf-8"))["owner"])

    def test_delivery_status_distinguishes_deposit_from_ack(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with redirect_stdout(StringIO()) as output:
                cli.coord_send(SimpleNamespace(coord_root=root, sender="codex", to="qoder", topic="T135", kind="context", text="evidence", text_file=None))
            mid = json.loads(output.getvalue())["id"]
            with redirect_stdout(StringIO()) as output:
                cli.coord_delivery_status(SimpleNamespace(coord_root=root, to="qoder", id=mid))
            self.assertEqual("DEPOSITED_UNCONFIRMED", json.loads(output.getvalue())["state"])
            with self.assertRaises(cli.ProtocolError):
                cli.coord_ack_message(SimpleNamespace(coord_root=root, agent="workbuddy", id=mid))
            with redirect_stdout(StringIO()):
                cli.coord_ack_message(SimpleNamespace(coord_root=root, agent="qoder", id=mid))
            with redirect_stdout(StringIO()) as output:
                cli.coord_delivery_status(SimpleNamespace(coord_root=root, to="qoder", id=mid))
            status = json.loads(output.getvalue())
            self.assertEqual("ACKNOWLEDGED", status["state"])
            self.assertFalse(status["execution_proven"])

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

    def test_coord_claim_scoped_path_uses_gate_and_leaves_no_record_on_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with redirect_stdout(StringIO()):
                cli.coord_claim(SimpleNamespace(
                    coord_root=root, agent="codex", task="T140-A", paths=["src/shared"], ttl_hours=48,
                ))
            output = StringIO()
            with redirect_stdout(output):
                cli.coord_claim(SimpleNamespace(
                    coord_root=root, agent="qoder", task="T140-B", paths=["src/shared/file.py"], ttl_hours=48,
                ))
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["claimed"])
            self.assertEqual("scope_conflict", payload["reason"])
            self.assertFalse((root / "claims" / "T140-B").exists())

    def test_coord_claim_task_only_stamps_a_lease_for_legacy_callers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = StringIO()
            with redirect_stdout(output):
                cli.coord_claim(SimpleNamespace(
                    coord_root=root, agent="codex", task="T141", paths=[], ttl_hours=6,
                ))
            payload = json.loads(output.getvalue())
            owner = json.loads((root / "claims" / "T141" / "owner.json").read_text(encoding="utf-8"))
            self.assertTrue(payload["claimed"])
            self.assertEqual("TASK_ONLY", owner["gate"]["status"])
            self.assertTrue(owner["expires_at"])

    def test_coord_claim_scoped_path_allows_expired_lease_and_reports_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = claim_conflicts.claim(
                root, agent="codex", task="T142-A", paths=["src/expired"],
                ttl_hours=1, now="2026-01-01T00:00:00+00:00",
            )
            self.assertTrue(old["claimed"])
            output = StringIO()
            with redirect_stdout(output):
                result = claim_conflicts.claim(
                    root, agent="qoder", task="T142-B", paths=["src/expired/file.py"],
                    ttl_hours=1, now="2026-01-02T00:00:00+00:00",
                )
            self.assertTrue(result["claimed"])
            self.assertEqual(["T142-A"], result["gate"]["ignored_expired"])

    def test_coord_claim_renew_is_owner_only_and_reachable_from_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with redirect_stdout(StringIO()):
                cli.coord_claim(SimpleNamespace(
                    coord_root=root, agent="codex", task="T143", paths=["src/lease.py"], ttl_hours=1,
                ))
            with self.assertRaisesRegex(cli.ProtocolError, "only the owner"):
                cli.coord_claim_renew(SimpleNamespace(
                    coord_root=root, agent="qoder", task="T143", ttl_hours=4,
                ))
            output = StringIO()
            with redirect_stdout(output):
                cli.coord_claim_renew(SimpleNamespace(
                    coord_root=root, agent="codex", task="T143", ttl_hours=4,
                ))
            payload = json.loads(output.getvalue())
            self.assertEqual("codex", payload["agent"])
            self.assertTrue(payload["expires_at"])

    def test_t132_claim_prevents_another_agent_overwriting_task_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with redirect_stdout(StringIO()):
                cli.coord_claim(SimpleNamespace(coord_root=root, agent="workbuddy", task="T132", paths=[]))
            def state_args(agent):
                return SimpleNamespace(
                    coord_root=root, agent=agent, task="T132", phase="working",
                    goal="isolated coordination rehearsal", evidence=[], blocked_reason="",
                    next_action="check receipt", wake_phrase=None, human_required=False,
                    stale_after_hours=12,
                )
            with self.assertRaisesRegex(cli.ProtocolError, "TASK_CLAIM_CONFLICT"):
                cli.coord_task_state(state_args("qoder"))
            self.assertFalse((root / "task_states" / "T132.json").exists())
            with redirect_stdout(StringIO()):
                cli.coord_task_state(state_args("workbuddy"))
            self.assertEqual("workbuddy", json.loads((root / "task_states" / "T132.json").read_text(encoding="utf-8"))["owner"])

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
