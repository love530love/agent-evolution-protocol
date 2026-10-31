"""Two schedulers must not be able to erase each other's work.

The lock is advisory, so these tests use real processes: an in-process test
would pass even if the lock did nothing, because ``_HELD`` re-entrancy would
short-circuit it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from agent_evolution_protocol.federation import engine, locking, store
from agent_evolution_protocol.federation.adapter import FakeAdapter


NOW = datetime(2026, 10, 31, 2, 0, tzinfo=timezone.utc)
CTX = multiprocessing.get_context("spawn")


def _bump(lock_file, counter_file, times):
    from agent_evolution_protocol.federation.locking import workspace_lock
    for _ in range(times):
        with workspace_lock(lock_file):
            data = json.loads(Path(counter_file).read_text(encoding="utf-8"))
            data["n"] += 1
            Path(counter_file).write_text(json.dumps(data), encoding="utf-8")


def _hold(lock_file, seconds, ready_file):
    from agent_evolution_protocol.federation.locking import workspace_lock
    with workspace_lock(lock_file):
        Path(ready_file).write_text("held", encoding="utf-8")
        time.sleep(seconds)


def _add_tasks(root, workspace_id, prefix, count):
    from agent_evolution_protocol.federation import engine as engine_mod
    for index in range(count):
        engine_mod.update(root, workspace_id,
                          lambda state: engine_mod.add_task(
                              root, state, task_id=f"{prefix}{index}", requirement="code"))


class CrossProcessExclusionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_processes_never_lose_an_increment(self):
        lock_file = self.folder / ".lock"
        counter = self.folder / "counter.json"
        counter.write_text(json.dumps({"n": 0}), encoding="utf-8")
        workers = [CTX.Process(target=_bump, args=(str(lock_file), str(counter), 40))
                   for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(60)
        self.assertEqual(json.loads(counter.read_text(encoding="utf-8"))["n"], 80)

    def test_a_busy_lock_times_out_instead_of_hanging(self):
        lock_file = self.folder / ".lock"
        ready = self.folder / "ready"
        holder = CTX.Process(target=_hold, args=(str(lock_file), 2.0, str(ready)))
        holder.start()
        try:
            deadline = time.monotonic() + 20
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(ready.exists(), "the holder never took the lock")
            with self.assertRaises(locking.LockTimeout):
                with locking.workspace_lock(str(lock_file), timeout=0.2):
                    pass
        finally:
            holder.join(30)


class ReentrancyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lock_file = Path(self.tmp.name) / ".lock"

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_process_may_take_the_lock_twice(self):
        with locking.workspace_lock(self.lock_file):
            with locking.workspace_lock(self.lock_file, timeout=0.1):
                pass
        self.assertEqual(locking._HELD, {})

    def test_lock_is_released_after_an_exception(self):
        with self.assertRaises(RuntimeError):
            with locking.workspace_lock(self.lock_file):
                raise RuntimeError("boom")
        self.assertEqual(locking._HELD, {})
        with locking.workspace_lock(self.lock_file, timeout=0.1):
            pass

    @unittest.skipUnless(os.name == "nt", "the first-byte pre-fill is Windows-only")
    def test_losing_the_first_byte_race_is_not_a_lock_failure(self):
        # Observed under a loaded full-suite run: between fstat seeing size 0
        # and the write that creates the first byte, another process can
        # create it and lock the region, so the write lands on a locked byte
        # and Windows answers EACCES.  The pre-fill is redundant by then, and
        # turning that into a lock failure would be worse than having no lock.
        real_write = os.write
        attempts = []

        def losing_the_race(handle, data):
            if not attempts:
                attempts.append(handle)
                raise PermissionError(13, "Permission denied")
            return real_write(handle, data)

        with mock.patch.object(locking.os, "write", side_effect=losing_the_race):
            with locking.workspace_lock(self.lock_file, timeout=1.0):
                pass
        self.assertEqual(len(attempts), 1)
        self.assertEqual(locking._HELD, {})


class EngineLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = engine.create(self.root, goal="ship it", acceptance=["tests pass"],
                                   moment=NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tick_reloads_state_written_by_another_actor(self):
        engine.add_task(self.root, self.state, task_id="t1", requirement="code", moment=NOW)
        other = engine.load(self.root, self.state["workspace_id"])
        engine.add_task(self.root, other, task_id="t2", requirement="code", moment=NOW)
        self.assertNotIn("t2", self.state["tasks"])

        adapters = [FakeAdapter("alpha", {"code"}, heartbeat_at=NOW.isoformat())]
        engine.tick(self.root, self.state, adapters, now=NOW, moment=NOW)

        self.assertIn("t2", self.state["tasks"])
        self.assertEqual(self.state["tasks"]["t1"]["owner"], "alpha")

    def test_concurrent_writers_keep_every_task(self):
        workspace_id = self.state["workspace_id"]
        workers = [CTX.Process(target=_add_tasks, args=(str(self.root), workspace_id, name, 5))
                   for name in ("a", "b")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(60)

        fresh = engine.load(self.root, workspace_id)
        self.assertEqual(len(fresh["tasks"]), 10, sorted(fresh["tasks"]))
        self.assertTrue(store.EventLog(self.root, workspace_id).verify()["valid"])

    def test_concurrent_event_appends_keep_a_contiguous_chain(self):
        workspace_id = self.state["workspace_id"]
        workers = [CTX.Process(target=_bump_events, args=(str(self.root), workspace_id, 10))
                   for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(60)

        log = store.EventLog(self.root, workspace_id)
        report = log.verify()
        self.assertTrue(report["valid"], report)
        seqs = [row["seq"] for row in log.events()]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))


def _bump_events(root, workspace_id, count):
    from agent_evolution_protocol.federation.store import EventLog
    log = EventLog(root, workspace_id)
    for index in range(count):
        log.append("concurrent", actor="worker", payload={"i": index})


if __name__ == "__main__":
    unittest.main()
