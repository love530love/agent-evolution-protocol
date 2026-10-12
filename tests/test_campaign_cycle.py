import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from datetime import timedelta
from unittest.mock import patch

from agent_evolution_protocol.coordination import campaign_cycle as cycle


class CampaignCycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name) / "coordination"
        self.bridge = Path(self.temp.name) / "bridge"
        self.team = dict(lead="codex", deputy="workbuddy", cross_platform="qoder",
                         catfish="hermes_desktop", reviewer="codebuddy")

    def tearDown(self):
        self.temp.cleanup()

    def open(self):
        return cycle.open_mission(self.root, self.bridge, task="T999", goal="solve the kernel",
                                  context=["errors/result.json"], **self.team)

    def test_open_deposits_three_parallel_jobs_without_claiming_ack(self):
        state = self.open()
        self.assertEqual(len(state["assignments"]), 3)
        self.assertTrue(all(item["status"] == "awaiting_ack" for item in state["assignments"]))
        self.assertEqual(len(list((self.bridge / "messages").glob("*/*.json"))), 3)
        self.assertFalse(state["upload_authorized"])

    def test_ack_requires_target_and_lead_failure_triggers_rescue(self):
        state = self.open()
        lead = next(item for item in state["assignments"] if item["role"] == "lead")
        with self.assertRaises(ValueError):
            cycle.ack(self.root, task="T999", assignment_id=lead["id"], agent="qoder")
        cycle.ack(self.root, task="T999", assignment_id=lead["id"], agent="codex")
        cycle.result(self.root, self.bridge, task="T999", assignment_id=lead["id"],
                     agent="codex", outcome="failed", evidence=["logs/compile.txt"],
                     hypothesis="compiler rejected reduction", next_step="split the kernel")
        state = cycle.status(self.root, "T999")["missions"][0]
        rescue = [row for row in state["assignments"] if row["role"] == "rescue"]
        self.assertEqual(len(rescue), 1)
        self.assertEqual(rescue[0]["agent"], "workbuddy")

    def test_timeout_handoff_is_idempotent_and_model_free(self):
        state = self.open()
        later = cycle.now() + timedelta(minutes=11)
        first = cycle.reconcile(self.root, self.bridge, at=later)
        second = cycle.reconcile(self.root, self.bridge, at=later)
        self.assertEqual(first["model_calls"], 0)
        self.assertTrue(first["actions"])
        self.assertEqual(second["actions"], [])

    def test_success_routes_independent_review(self):
        state = self.open()
        lead = next(item for item in state["assignments"] if item["role"] == "lead")
        cycle.ack(self.root, task="T999", assignment_id=lead["id"], agent="codex")
        cycle.result(self.root, self.bridge, task="T999", assignment_id=lead["id"],
                     agent="codex", outcome="completed", evidence=["logs/local.txt"],
                     hypothesis="split kernel passes locally", next_step="independent regression")
        state = cycle.status(self.root, "T999")["missions"][0]
        review = next(item for item in state["assignments"] if item["role"] == "reviewer")
        self.assertEqual(review["agent"], "codebuddy")
        self.assertEqual(review["status"], "awaiting_ack")

    def test_reconcile_repairs_interrupted_notification_once(self):
        state = self.open()
        item = state["assignments"][0]
        state["assignments"][0]["wake_status"] = "PENDING"
        cycle._atomic(cycle._state_path(self.root, "T999"), state)
        first = cycle.reconcile(self.root, self.bridge)
        second = cycle.reconcile(self.root, self.bridge)
        self.assertIn("notification_repaired", [row["event"] for row in first["actions"]])
        self.assertEqual(second["actions"], [])

    def test_capacity_queue_releases_after_evidenced_result(self):
        state = self.open()
        lead = next(item for item in state["assignments"] if item["role"] == "lead")
        second = cycle.assign(self.root, self.bridge, task="T999", agent="codex", role="lead",
                              objective="independent kernel variant", cause="variant:2")
        third = cycle.assign(self.root, self.bridge, task="T999", agent="codex", role="lead",
                             objective="another kernel variant", cause="variant:3")
        self.assertEqual(second["status"], "awaiting_ack")
        self.assertEqual(third["status"], "queued_capacity")
        self.assertFalse((self.bridge / "messages" / "codex" / f"{third['id']}.json").exists())
        cycle.ack(self.root, task="T999", assignment_id=lead["id"], agent="codex")
        cycle.result(self.root, self.bridge, task="T999", assignment_id=lead["id"],
                     agent="codex", outcome="completed", evidence=["logs/variant.txt"],
                     hypothesis="variant passes", next_step="review")
        cycle.reconcile(self.root, self.bridge)
        state = cycle.status(self.root, "T999")["missions"][0]
        activated = next(item for item in state["assignments"] if item["id"] == third["id"])
        self.assertEqual(activated["status"], "awaiting_ack")
        self.assertTrue((self.bridge / "messages" / "codex" / f"{third['id']}.json").exists())
        score = {row["agent"]: row for row in cycle.scoreboard(self.root)["rows"]}
        self.assertEqual(score["codex"]["score"], 3)

    def test_repeat_official_failure_creates_hold_and_distinct_reviews(self):
        self.open()
        for submission in ("s1", "s2"):
            path = Path(self.temp.name) / f"{submission}.json"
            path.write_text(json.dumps({"submission_id": submission,
                "chips": {"xpu": {"status": "failed", "failure_signature": "MLIR pass",
                                  "raw_error": "TritonXPULegalize failed"}}}), encoding="utf-8")
            cycle.official_return(self.root, self.bridge, task="T999", submission_id=submission,
                                  package_sha256="a" * 64, snapshot_path=path)
        state = cycle.status(self.root, "T999")["missions"][0]
        self.assertEqual(len(state["returns"]), 2)
        self.assertEqual(len(state["holds"]), 1)
        self.assertEqual(len([row for row in state["assignments"] if row["cause"].startswith("return:")]), 6)
        self.assertFalse(state["upload_authorized"])

    def test_duplicate_submission_is_not_redispatched(self):
        self.open()
        path = Path(self.temp.name) / "return.json"
        path.write_text(json.dumps({"submission_id": "s1", "chips": {"nvidia": {"status": "passed"}}}),
                        encoding="utf-8")
        first = cycle.official_return(self.root, self.bridge, task="T999", submission_id="s1",
                                      package_sha256="b" * 64, snapshot_path=path)
        second = cycle.official_return(self.root, self.bridge, task="T999", submission_id="s1",
                                       package_sha256="b" * 64, snapshot_path=path)
        self.assertEqual(first, second)
        self.assertEqual(len(cycle.status(self.root, "T999")["missions"][0]["returns"]), 1)

    def test_retry_repairs_partial_return_dispatch(self):
        self.open()
        path = Path(self.temp.name) / "partial.json"
        path.write_text(json.dumps({"submission_id": "partial", "chips": {
            "xpu": {"status": "failed", "failure_signature": "MLIR", "raw_error": "pass failed"}}}),
            encoding="utf-8")
        original = cycle._assign_locked
        calls = 0

        def interrupt_after_first(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated process interruption")
            return original(*args, **kwargs)

        with patch.object(cycle, "_assign_locked", side_effect=interrupt_after_first):
            with self.assertRaises(RuntimeError):
                cycle.official_return(self.root, self.bridge, task="T999", submission_id="partial",
                                      package_sha256="c" * 64, snapshot_path=path)
        cycle.official_return(self.root, self.bridge, task="T999", submission_id="partial",
                              package_sha256="c" * 64, snapshot_path=path)
        state = cycle.status(self.root, "T999")["missions"][0]
        self.assertEqual(len(state["returns"]), 1)
        self.assertEqual(len([row for row in state["assignments"] if row["cause"].startswith("return:partial:")]), 3)


if __name__ == "__main__":
    unittest.main()
