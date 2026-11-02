"""P0 tests for claim_conflicts atomic claim (F-03).

Verifies that:
1. The claim() operation is now atomic within the workspace lock.
2. Concurrent processes cannot both claim the same task.
3. The lock file is properly managed.
"""

import json
import multiprocessing
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent_evolution_protocol.coordination.claim_conflicts as claim_conflicts


def _claim_worker(coord_root, agent, task, paths, results, index):
    """Worker process that attempts to claim a task."""
    try:
        result = claim_conflicts.claim(coord_root, agent=agent, task=task,
                                       paths=paths, ttl_hours=24)
        results[index] = result
    except Exception as e:
        results[index] = {"error": str(e)}


class TestClaimAtomicity(unittest.TestCase):
    """Test that claim() is atomic and prevents race conditions."""

    def test_sequential_claims_work(self):
        """Two sequential claims for different tasks should both succeed."""
        with tempfile.TemporaryDirectory() as tmp:
            coord_root = Path(tmp)
            paths1 = ["src/agent_evolution_protocol/coordination/ack_protocol.py"]
            paths2 = ["src/agent_evolution_protocol/coordination/transcript_bus.py"]

            result1 = claim_conflicts.claim(coord_root, agent="agent-1",
                                            task="AEP-TASK-1", paths=paths1,
                                            ttl_hours=24)
            self.assertTrue(result1["claimed"])

            result2 = claim_conflicts.claim(coord_root, agent="agent-2",
                                            task="AEP-TASK-2", paths=paths2,
                                            ttl_hours=24)
            self.assertTrue(result2["claimed"])

    def test_duplicate_task_claim_fails(self):
        """A second claim for the same task should fail."""
        with tempfile.TemporaryDirectory() as tmp:
            coord_root = Path(tmp)
            paths = ["src/agent_evolution_protocol/coordination/ack_protocol.py"]

            result1 = claim_conflicts.claim(coord_root, agent="agent-1",
                                            task="AEP-TASK-1", paths=paths,
                                            ttl_hours=24)
            self.assertTrue(result1["claimed"])

            result2 = claim_conflicts.claim(coord_root, agent="agent-2",
                                            task="AEP-TASK-1", paths=paths,
                                            ttl_hours=24)
            self.assertFalse(result2["claimed"])
            self.assertEqual(result2["reason"], "task_already_claimed")

    def test_concurrent_claims_race(self):
        """Two concurrent processes claiming the same task: only one should succeed.
        
        This test runs 10 processes concurrently. Due to the workspace lock,
        only one should succeed, and the rest should get task_already_claimed.
        
        Note: multiprocessing spawn mode is used to avoid WSL fork issues.
        """
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        
        with tempfile.TemporaryDirectory() as tmp:
            coord_root = Path(tmp)
            paths = ["src/agent_evolution_protocol/coordination/ack_protocol.py"]
            results = mp.Manager().list([None] * 10)
            processes = []

            for i in range(10):
                p = ctx.Process(
                    target=_claim_worker,
                    args=(coord_root, f"agent-{i}", "AEP-RACE-1", paths,
                          results, i)
                )
                processes.append(p)

            # Start all processes at roughly the same time.
            for p in processes:
                p.start()
            for p in processes:
                p.join(timeout=30)

            # Count how many succeeded.
            claimed_count = sum(1 for r in results if r and r.get("claimed"))
            failed_count = sum(1 for r in results if r and not r.get("claimed"))

            # With the workspace lock, only ONE should succeed.
            self.assertEqual(claimed_count, 1,
                             f"Expected exactly 1 claim to succeed, got {claimed_count}")
            self.assertEqual(failed_count, 9,
                             f"Expected 9 claims to fail, got {failed_count}")

            # The winner should be one of our agents.
            winner = next(r for r in results if r and r.get("claimed"))
            self.assertIn(winner["agent"], [f"agent-{i}" for i in range(10)])


class TestClaimLockManagement(unittest.TestCase):
    """Test that lock files are properly cleaned up."""

    def test_lock_file_not_permanent(self):
        """After a claim succeeds, the lock file should not block future operations."""
        with tempfile.TemporaryDirectory() as tmp:
            coord_root = Path(tmp)
            paths1 = ["src/agent_evolution_protocol/coordination/ack_protocol.py"]
            paths2 = ["src/agent_evolution_protocol/coordination/transcript_bus.py"]

            # First claim.
            result1 = claim_conflicts.claim(coord_root, agent="agent-1",
                                            task="AEP-TASK-1", paths=paths1,
                                            ttl_hours=24)
            self.assertTrue(result1["claimed"])

            # Second claim for a different task should work immediately.
            result2 = claim_conflicts.claim(coord_root, agent="agent-2",
                                            task="AEP-TASK-2", paths=paths2,
                                            ttl_hours=24)
            self.assertTrue(result2["claimed"])


if __name__ == "__main__":
    import agent_evolution_protocol.coordination.claim_conflicts as claim_conflicts
    unittest.main()
