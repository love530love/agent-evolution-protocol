"""Integration checks for actual validation evidence crossing a worker handoff."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from agent_evolution_protocol import cli
from agent_evolution_protocol.coordination import (
    commander,
    improvement,
    improvement_dispatch,
    improvement_policy,
    improvement_runs,
    improvement_work,
)


class ValidationHandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        self.root = folder / "coordination"
        self.workspace = folder / "workspace"
        (self.workspace / "tests").mkdir(parents=True)
        self.fixture = self.workspace / "tests" / "test_fixture.py"
        self.fixture.write_text(
            "import unittest\nclass Fixture(unittest.TestCase):\n"
            "    def test_result(self):\n        self.assertEqual(1, 2)\n",
            encoding="utf-8",
        )
        proof = folder / "baseline.txt"
        proof.write_text("Isolated handoff fixture.\n", encoding="utf-8")
        self.identity = improvement.propose(self.root, {
            "author": "author", "change": "record actual validation outcomes",
            "acceptance": "a successor sees the failed run and its evidence",
            "scope": "isolated fixture", "rollback": "discard fixture",
            "evidence": [str(proof)],
        })["id"]
        improvement_policy.configure(
            self.root, mode="enforced", actor="operator", note="isolated acceptance",
        )
        self.lease = improvement_work.claim(self.root, self.identity, actor="first")

    def run_validation(self):
        return improvement.validate_tests(
            self.root, self.identity, workspace=self.workspace,
            actor="first", token=self.lease["token"], timeout=20,
        )

    def release(self):
        return improvement_work.finish(
            self.root, self.identity, actor="first", token=self.lease["token"],
            release=True,
        )

    def make_fixture_pass(self):
        self.fixture.write_text(
            "import unittest\nclass Fixture(unittest.TestCase):\n"
            "    def test_result(self):\n        self.assertEqual(1, 1)\n",
            encoding="utf-8",
        )

    def interrupt_validation(self):
        with patch.object(improvement.subprocess, "run", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_validation()

    def test_real_failure_is_visible_to_replacement_claimant(self):
        result = self.run_validation()
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["tests_run"], 1)
        self.assertEqual(result["proposal"]["state"], "PROPOSED")
        self.assertTrue(Path(result["execution_receipt"]).is_file())
        self.release()

        successor = improvement_work.claim(self.root, self.identity, actor="second")
        packet = improvement_work.brief(
            self.root, self.identity, actor="second", token=successor["token"],
        )
        self.assertEqual(len(packet["validation_runs"]), 1)
        saved = packet["validation_runs"][0]
        self.assertEqual(saved["actor"], "first")
        self.assertEqual(saved["head"], self.lease["head"])
        self.assertEqual(saved["run_id"], result["run_id"])
        self.assertEqual(saved["status"], "FAILED")
        self.assertEqual(saved["test_status"], "FAILED")
        self.assertEqual(saved["tests_run"], 1)
        self.assertEqual(Path(saved["report"]), Path(result["report"]))
        self.assertIn("AssertionError", Path(saved["output"]).read_text(encoding="utf-8"))
        self.assertNotIn(self.lease["token"], json.dumps(packet))
        self.assertFalse(packet["execution_stopped_verified"])
        self.assertFalse(packet["execution_started"])
        inspection = improvement_work.inspect_handoff(self.root, self.identity)
        self.assertEqual(inspection["validation_run_count"], 1)

    def test_late_lease_expiry_preserves_test_pass_and_blocked_promotion(self):
        self.make_fixture_pass()
        original_run = improvement.subprocess.run
        clock = [self.lease["claimed_at"]]

        def expire_after_actual_test(*args, **kwargs):
            completed = original_run(*args, **kwargs)
            clock[0] = self.lease["expires"] + 1
            return completed

        with patch.object(improvement_work, "_now", side_effect=lambda: clock[0]):
            with patch.object(improvement.subprocess, "run", side_effect=expire_after_actual_test):
                result = self.run_validation()
            self.assertEqual(result["status"], "PROMOTION_BLOCKED")
            self.assertEqual(result["proposal"]["state"], "PROPOSED")
            self.assertEqual(json.loads(Path(result["report"]).read_text(encoding="utf-8"))["status"], "PASSED")
            successor = improvement_work.claim(self.root, self.identity, actor="second")
            packet = improvement_work.brief(
                self.root, self.identity, actor="second", token=successor["token"],
            )
        saved = packet["validation_runs"][0]
        self.assertEqual(saved["status"], "PROMOTION_BLOCKED")
        self.assertEqual(saved["test_status"], "PASSED")
        self.assertEqual(saved["tests_run"], 1)
        self.assertEqual(saved["actor"], "first")
        self.assertFalse(packet["execution_stopped_verified"])

    def assert_damaged_run_blocks_reclaim(self, damaged_path):
        damaged_path.write_text("corrupted evidence\n", encoding="utf-8")
        prior_lease = improvement_work._path(self.root, self.identity).read_bytes()
        with patch.object(improvement_work, "_now", return_value=self.lease["expires"] + 1):
            with self.assertRaises(ValueError):
                improvement_work.claim(self.root, self.identity, actor="second")
        self.assertEqual(improvement_work._path(self.root, self.identity).read_bytes(), prior_lease)
        history = improvement_runs.history(self.root, self.identity)
        self.assertFalse(history["handoff_ready"])
        self.assertTrue(history["blocked_runs"])
        inspection = improvement_work.inspect_handoff(self.root, self.identity)
        self.assertIn("VALIDATION_RUN_UNUSABLE", [issue["code"] for issue in inspection["issues"]])

    def test_modified_output_blocks_claim_without_replacing_previous_lease(self):
        self.run_validation()
        saved = improvement_runs.history(self.root, self.identity)["runs"][0]
        self.assert_damaged_run_blocks_reclaim(Path(saved["output"]))

    def test_corrupt_receipt_blocks_claim_without_replacing_previous_lease(self):
        result = self.run_validation()
        self.assert_damaged_run_blocks_reclaim(Path(result["execution_receipt"]))

    def test_interrupted_run_blocks_repeat_execution_before_subprocess(self):
        self.interrupt_validation()
        history = improvement_runs.history(self.root, self.identity)
        self.assertFalse(history["handoff_ready"])
        self.assertTrue(history["blocked_runs"])
        self.assertEqual(len(history["runs"]), 1)
        self.assertEqual(history["runs"][0]["status"], "INCOMPLETE")
        run_folder = self.root / "improvements" / self.identity / "runs" / history["runs"][0]["run_id"]
        self.assertTrue((run_folder / "request.json").is_file())
        self.assertFalse((run_folder / "receipt.json").exists())
        with patch.object(improvement.subprocess, "run") as execute:
            with self.assertRaises(ValueError):
                self.run_validation()
            execute.assert_not_called()

    def test_overlapping_same_token_validation_starts_only_one_subprocess(self):
        entered = Event()
        proceed = Event()
        original_run = improvement.subprocess.run

        def held_actual_test(*args, **kwargs):
            entered.set()
            if not proceed.wait(5):
                raise RuntimeError("test did not release the held validation")
            return original_run(*args, **kwargs)

        with patch.object(improvement.subprocess, "run", side_effect=held_actual_test) as execute:
            with ThreadPoolExecutor(max_workers=1) as pool:
                running = pool.submit(self.run_validation)
                try:
                    self.assertTrue(entered.wait(5), "first validation did not start")
                    with self.assertRaises(ValueError):
                        self.run_validation()
                    self.assertEqual(execute.call_count, 1)
                finally:
                    proceed.set()
                result = running.result(timeout=20)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(len(improvement_runs.history(self.root, self.identity)["runs"]), 1)

    def test_successful_previous_stage_is_historical_in_reviewer_brief(self):
        self.make_fixture_pass()
        result = self.run_validation()
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["proposal"]["state"], "VALIDATED")
        history = improvement_runs.history(self.root, self.identity)
        self.assertEqual(history["runs"], [])
        self.assertEqual(history["historical_count"], 1)
        self.assertTrue(history["handoff_ready"])
        reviewer = improvement_work.claim(self.root, self.identity, actor="reviewer")
        packet = improvement_work.brief(
            self.root, self.identity, actor="reviewer", token=reviewer["token"],
        )
        self.assertEqual(packet["action"], "review")
        self.assertEqual(packet["validation_runs"], [])
        self.assertTrue(any(Path(item["path"]) == Path(result["report"]) for item in packet["evidence"]))

    def test_blocked_cli_is_read_only_returns_exit_two_and_has_no_token(self):
        self.interrupt_validation()
        before = {path.relative_to(self.root): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        args = cli.build_parser().parse_args([
            "--coord-root", str(self.root), "coord-improvement", "validation-runs",
            "--id", self.identity,
        ])
        with redirect_stdout(StringIO()) as output:
            with self.assertRaises(SystemExit) as stopped:
                args.func(args)
        self.assertEqual(stopped.exception.code, 2)
        packet = json.loads(output.getvalue())
        self.assertFalse(packet["handoff_ready"])
        self.assertEqual(packet["runs"][0]["status"], "INCOMPLETE")
        self.assertNotIn(self.lease["token"], output.getvalue())
        self.assertFalse(packet["execution_started"])
        after = {path.relative_to(self.root): path.read_bytes()
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_legacy_report_remains_explicitly_unverified_and_allows_handoff(self):
        legacy = self.root / "improvements" / self.identity / "runs" / ("f" * 32)
        legacy.mkdir(parents=True)
        legacy_report = {"status": "FAILED", "tests_run": 1, "exit_code": 1}
        (legacy / "report.json").write_text(json.dumps(legacy_report), encoding="utf-8")
        (legacy / "output.txt").write_text("Historical unbound report.\n", encoding="utf-8")
        self.release()
        successor = improvement_work.claim(self.root, self.identity, actor="second")
        packet = improvement_work.brief(
            self.root, self.identity, actor="second", token=successor["token"],
        )
        self.assertEqual(packet["validation_runs"], [])
        self.assertEqual(len(packet["legacy_validation_runs"]), 1)
        saved = packet["legacy_validation_runs"][0]
        self.assertEqual(saved["status"], "LEGACY_UNVERIFIED")
        self.assertNotIn("actor", saved)
        self.assertNotIn("head", saved)
        self.assertFalse(saved["process_tree_stopped_verified"])
        self.assertFalse((legacy / "request.json").exists())
        self.assertFalse((legacy / "receipt.json").exists())

    def test_pending_prevents_routing_an_unresolved_run_after_release(self):
        self.interrupt_validation()
        self.release()
        pending = improvement_work.pending(self.root)["work"]
        self.assertEqual(len(pending), 1)
        self.assertFalse(pending[0]["available"])
        self.assertIn("validation", pending[0]["error"])
        roster = {"agents": [{"agent": "second", "actions": ["validate"]}]}
        observed = {"agents": [{"agent": "second", "state": "online", "active_assignments": []}]}
        with patch.object(improvement_dispatch.presence, "snapshot", return_value=observed):
            with patch.object(commander, "submit_order") as submit:
                plan = improvement_dispatch.schedule(self.root, roster, apply=True)
                submit.assert_not_called()
        self.assertEqual(plan["orders_queued"], 0)
        self.assertEqual(plan["assignments"][0]["status"], "WORK_DAMAGED")

    def test_unresolved_active_owner_is_not_assigned_another_proposal(self):
        self.interrupt_validation()
        improvement.propose(self.root, {
            "author": "another-author", "change": "independent work",
            "acceptance": "must not reuse the active validator", "scope": "fixture",
            "rollback": "discard fixture", "evidence": [str(self.root.parent / "baseline.txt")],
        })
        pending = improvement_work.pending(self.root)["work"]
        blocked = next(item for item in pending if item["id"] == self.identity)
        self.assertFalse(blocked["available"])
        self.assertEqual(blocked.get("owner"), "first")
        roster = {"agents": [{"agent": "first", "actions": ["validate"]}]}
        observed = {"agents": [{"agent": "first", "state": "online", "active_assignments": []}]}
        with patch.object(improvement_dispatch.presence, "snapshot", return_value=observed):
            with patch.object(commander, "submit_order") as submit:
                plan = improvement_dispatch.schedule(self.root, roster, apply=True)
                submit.assert_not_called()
        self.assertEqual(plan["orders_queued"], 0)
        healthy = next(item for item in plan["assignments"] if item["id"] != self.identity)
        self.assertEqual(healthy["status"], "NO_ELIGIBLE_MEMBER")
        self.assertEqual(healthy["candidates"][0]["reasons"], ["KNOWN_ASSIGNMENT_OR_ORDER"])

        # Expiry does not prove the old process stopped; preserve its separate
        # execution occupancy even when there is no longer a valid lease owner.
        with patch.object(improvement_work, "_now", return_value=self.lease["expires"] + 1):
            pending = improvement_work.pending(self.root)["work"]
            blocked = next(item for item in pending if item["id"] == self.identity)
            self.assertIsNone(blocked["owner"])
            self.assertEqual(blocked["execution_owners"], ["first"])
            with patch.object(improvement_dispatch.presence, "snapshot", return_value=observed):
                with patch.object(commander, "submit_order") as submit:
                    plan = improvement_dispatch.schedule(self.root, roster, apply=True)
                    submit.assert_not_called()

    def test_damaged_legacy_report_is_not_accepted_as_compatible(self):
        legacy = self.root / "improvements" / self.identity / "runs" / ("f" * 32)
        legacy.mkdir(parents=True)
        (legacy / "report.json").write_text('{"status":', encoding="utf-8")
        (legacy / "output.txt").write_text("partial output", encoding="utf-8")
        self.assertFalse(improvement_runs.history(self.root, self.identity)["handoff_ready"])
        self.release()
        with self.assertRaises(ValueError):
            improvement_work.claim(self.root, self.identity, actor="second")

    def test_compatibility_mode_cannot_bypass_active_claim_without_token(self):
        improvement_policy.configure(self.root, mode="cooperative", actor="operator", note="fixture compatibility")
        with patch.object(improvement.subprocess, "run") as execute:
            with self.assertRaisesRegex(ValueError, "active claim requires"):
                improvement.validate_tests(self.root, self.identity, workspace=self.workspace, actor="unclaimed")
            execute.assert_not_called()
        self.assertEqual(improvement_runs.history(self.root, self.identity)["runs"], [])
