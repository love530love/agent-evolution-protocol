"""A queue that keeps working when every agent is offline.

The failure this file is written against is a queue that reports success from
a process exit, or silently replays a command whose first run already wrote
half of its output.  Each test therefore checks both the state and the evidence
behind it.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_evolution_protocol.coordination import unattended_queue as queue
from agent_evolution_protocol.federation import engine
from agent_evolution_protocol.federation.locking import workspace_lock


def _manifest(workspace: Path, task_id: str, code: str, **extra) -> dict:
    value = {"task_id": task_id, "workspace": str(workspace), "goal": task_id,
             "argv": [sys.executable, "-c", code]}
    value.update(extra)
    return value


class QueueValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_task_id_must_be_a_safe_name(self):
        with self.assertRaises(ValueError):
            queue.enqueue(self.root, _manifest(self.root, "../escape", "pass"))

    def test_workspace_must_exist(self):
        with self.assertRaises(ValueError):
            queue.enqueue(self.root, _manifest(self.root / "missing", "T1", "pass"))

    def test_expected_output_cannot_escape_the_workspace(self):
        with self.assertRaises(ValueError):
            queue.enqueue(self.root, _manifest(self.root, "T1", "pass",
                                               expected_outputs=["../outside.txt"]))

    def test_the_same_manifest_is_idempotent_and_a_different_one_conflicts(self):
        first = queue.enqueue(self.root, _manifest(self.root, "T1", "pass"))
        second = queue.enqueue(self.root, _manifest(self.root, "T1", "pass"))
        self.assertEqual(first["state"], "QUEUED")
        self.assertEqual(second["state"], "ALREADY_QUEUED")
        with self.assertRaises(ValueError):
            queue.enqueue(self.root, _manifest(self.root, "T1", "raise SystemExit(2)"))

    def test_a_late_dependency_that_closes_a_cycle_is_rejected(self):
        queue.enqueue(self.root, _manifest(self.root, "A", "pass", depends_on=["B"]))
        with self.assertRaises(ValueError):
            queue.enqueue(self.root, _manifest(self.root, "B", "pass", depends_on=["A"]))


class QueueRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_a_zero_exit_with_the_declared_file_is_succeeded_with_a_hash(self):
        queue.enqueue(self.root, _manifest(
            self.workspace, "T1",
            "from pathlib import Path; Path('out.txt').write_text('done', encoding='utf-8')",
            expected_outputs=["out.txt"]))
        receipt = queue.run_once(self.root, task_id="T1")
        self.assertEqual(receipt["state"], "SUCCEEDED")
        self.assertEqual(receipt["outputs"][0]["path"], "out.txt")
        self.assertEqual(receipt["outputs"][0]["bytes"], 4)
        self.assertIn("not an agent ACK", receipt["note"])
        self.assertFalse(receipt["replay_allowed"])

    def test_a_zero_exit_without_the_declared_file_fails(self):
        queue.enqueue(self.root, _manifest(self.workspace, "T1", "pass",
                                           expected_outputs=["missing.txt"]))
        receipt = queue.run_once(self.root, task_id="T1")
        self.assertEqual(receipt["state"], "FAILED")
        self.assertIn("missing_expected_outputs", receipt["error"])
        self.assertEqual(receipt["missing_outputs"], ["missing.txt"])

    def test_a_nonzero_exit_is_terminal_and_is_not_retried(self):
        queue.enqueue(self.root, _manifest(self.workspace, "T1", "raise SystemExit(3)"))
        first = queue.run_once(self.root, task_id="T1")
        self.assertEqual(first["state"], "FAILED")
        self.assertEqual(first["error"], "process_exit_3")
        second = queue.run_once(self.root, task_id="T1")
        self.assertEqual(second["state"], "FAILED")
        self.assertEqual(second["attempt"], 1)

    def test_a_timeout_is_unknown_and_does_not_replay(self):
        queue.enqueue(self.root, _manifest(
            self.workspace, "T1", "import time; time.sleep(5)", timeout_seconds=0.2))
        first = queue.run_once(self.root, task_id="T1")
        self.assertEqual(first["state"], "UNKNOWN")
        self.assertTrue(first["timed_out"])
        self.assertFalse(first["replay_allowed"])
        second = queue.run_once(self.root, task_id="T1")
        self.assertEqual(second["state"], "UNKNOWN")
        self.assertEqual(second["attempt"], 1)

    def test_a_failed_dependency_blocks_its_dependent(self):
        queue.enqueue(self.root, _manifest(self.workspace, "A", "raise SystemExit(4)"))
        queue.enqueue(self.root, _manifest(self.workspace, "B", "pass", depends_on=["A"]))
        queue.run_once(self.root, task_id="A")
        blocked = queue.run_once(self.root, task_id="B")
        self.assertEqual(blocked["state"], "BLOCKED")
        self.assertIn("dependency_FAILED:A", blocked["error"])

    def test_a_blocked_dependent_runs_after_its_dependency_is_requeued(self):
        first = ("from pathlib import Path; import sys; p=Path('a_marker'); "
                 "sys.exit(0 if p.exists() else (p.write_text('x') or 1))")
        queue.enqueue(self.root, _manifest(self.workspace, "A", first))
        queue.enqueue(self.root, _manifest(
            self.workspace, "B",
            "from pathlib import Path; Path('b.txt').write_text('b', encoding='utf-8')",
            depends_on=["A"], expected_outputs=["b.txt"]))
        self.assertEqual(queue.run_once(self.root, task_id="A")["state"], "FAILED")
        self.assertEqual(queue.run_once(self.root, task_id="B")["state"], "BLOCKED")
        queue.requeue(self.root, "A", actor="operator", reason="marker proves first run was safe")
        self.assertEqual(queue.run_once(self.root, task_id="A")["state"], "SUCCEEDED")
        self.assertEqual(queue.run_once(self.root)["state"], "SUCCEEDED")
        self.assertEqual(queue.status(self.root)["counts"]["SUCCEEDED"], 2)

    def test_requeue_uses_a_new_event_id_and_keeps_the_first_attempt(self):
        code = ("from pathlib import Path; import sys; p=Path('marker'); "
                "sys.exit(0 if p.exists() else (p.write_text('x') or 1))")
        queue.enqueue(self.root, _manifest(self.workspace, "T1", code))
        first = queue.run_once(self.root, task_id="T1")
        self.assertEqual(first["state"], "FAILED")
        self.assertEqual(first["event_id"], "UNATTENDED-T1")
        queue.requeue(self.root, "T1", actor="operator", reason="marker proves no output")
        second = queue.run_once(self.root, task_id="T1")
        self.assertEqual(second["state"], "SUCCEEDED")
        self.assertEqual(second["event_id"], "UNATTENDED-T1-R2")
        self.assertEqual(second["history"][0]["state"], "FAILED")

    def test_unknown_cannot_be_requeued_without_explicit_consent(self):
        queue.enqueue(self.root, _manifest(
            self.workspace, "T1", "import time; time.sleep(5)", timeout_seconds=0.2))
        queue.run_once(self.root, task_id="T1")
        with self.assertRaises(ValueError):
            queue.requeue(self.root, "T1", actor="operator", reason="maybe safe")

    def test_a_cancelled_task_is_not_executed(self):
        queue.enqueue(self.root, _manifest(self.workspace, "T1", "pass"))
        queue.cancel(self.root, "T1", actor="operator", reason="superseded")
        receipt = queue.run_once(self.root, task_id="T1")
        self.assertEqual(receipt["state"], "CANCELLED")

    def test_a_second_runner_does_not_execute_before_the_lock_is_free(self):
        queue.enqueue(self.root, _manifest(self.workspace, "T1", "pass"))
        lock = self.root / "unattended" / "runner.lock"
        entered = threading.Event()
        release = threading.Event()

        def hold():
            with workspace_lock(lock, timeout=2):
                entered.set()
                release.wait(2)

        thread = threading.Thread(target=hold, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(2))
        try:
            result = queue.run_once(self.root, task_id="T1")
            self.assertEqual(result["state"], "RUNNER_BUSY")
        finally:
            release.set()
            thread.join(2)

    def test_serve_drains_a_ready_task_and_reports_idle(self):
        queue.enqueue(self.root, _manifest(
            self.workspace, "A",
            "from pathlib import Path; Path('a.txt').write_text('a', encoding='utf-8')",
            expected_outputs=["a.txt"]))
        result = queue.serve(self.root)
        self.assertEqual(result["last"]["state"], "IDLE")
        self.assertEqual(result["ticks"], 2)
        self.assertEqual(result["status"]["counts"]["SUCCEEDED"], 1)

    def test_serve_stops_before_a_tick_when_the_stop_file_exists(self):
        queue.enqueue(self.root, _manifest(self.workspace, "A", "pass"))
        stop = self.root / "stop"
        stop.write_text("stop", encoding="utf-8")
        result = queue.serve(self.root, stop_file=stop)
        self.assertEqual(result["last"]["state"], "STOPPED")
        self.assertEqual(result["ticks"], 0)
        self.assertEqual(result["status"]["counts"]["QUEUED"], 1)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.coord = self.root / "coordination"
        self.coord.mkdir()
        self.federation = self.root / "federation"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.workspace_id = engine.create(
            self.federation, goal="test", acceptance=["done"])["workspace_id"]

    def tearDown(self):
        self.temp.cleanup()

    def _snapshot(self, state: str) -> dict:
        return {"agents": [{"agent": "agent_a", "state": state}],
                "strength": {"online": 1 if state == "online" else 0,
                             "offline": 1 if state == "offline" else 0, "stale": 0}}

    def test_an_online_agent_gets_an_assignment_not_a_local_run(self):
        result = queue.dispatch_or_queue(
            self.coord, self.federation, workspace_id=self.workspace_id,
            task_id="T1", requirement="do it", agents=["agent_a"],
            local_manifest=_manifest(self.workspace, "T1-local", "pass"),
            presence_snapshot=self._snapshot("online"))
        self.assertEqual(result["route"], "agent")
        self.assertEqual(result["target"], "agent_a")
        self.assertIn("not acceptance", result["note"])
        self.assertFalse((self.coord / "unattended").exists())
        self.assertTrue((self.coord / "commander" / "inbox").is_dir(),
                        "order must land in the coord mailbox")
        self.assertEqual(engine.load(self.federation, self.workspace_id)["tasks"]["T1"]["owner"], "agent_a")

    def test_an_offline_agent_leaves_the_explicit_local_plan_queued(self):
        result = queue.dispatch_or_queue(
            self.coord, self.federation, workspace_id=self.workspace_id,
            task_id="T1", requirement="do it", agents=["agent_a"],
            local_manifest=_manifest(self.workspace, "T1-local", "pass"),
            presence_snapshot=self._snapshot("offline"))
        self.assertEqual(result["route"], "local")
        self.assertEqual(result["queue"]["state"], "QUEUED")
        status = queue.status(self.coord)
        self.assertEqual(status["tasks"][0]["task_id"], "T1-local")

    def test_offline_without_a_local_plan_stays_waiting(self):
        result = queue.dispatch_or_queue(
            self.coord, self.federation, workspace_id=self.workspace_id,
            task_id="T1", requirement="do it", agents=["agent_a"],
            presence_snapshot=self._snapshot("offline"))
        self.assertEqual(result["route"], "waiting")
        self.assertEqual(result["reason"], "no_online_agent_and_no_local_plan")


if __name__ == "__main__":
    unittest.main()
