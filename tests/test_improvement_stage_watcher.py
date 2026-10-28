"""Real Windows event delivery; fixtures never send input to an Agent session."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from uuid import uuid4

from agent_evolution_protocol.coordination import commander, improvement, improvement_runs, improvement_work
from agent_evolution_protocol.coordination.battle_orders import _atomic_json, _task_writer_lock


REPO = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh.exe")


@unittest.skipUnless(sys.platform == "win32" and PWSH, "Windows PowerShell 7 required")
class ImprovementStageWatcherTests(unittest.TestCase):
    def _proposal(self, root):
        proof = root / "proof.txt"
        proof.write_text("immutable watcher fixture", encoding="utf-8")
        identity = improvement.propose(root, dict(author="fixture-author", change="fixture change",
            acceptance="local tests", rollback="discard fixture", scope="temporary directory only",
            evidence=[str(proof)]))["id"]
        return identity, proof

    def _roster(self, root, action):
        # Do not call presence.signal: a fixture declaration is not a real Agent.
        folder = root / "presence" / "agents"
        folder.mkdir(parents=True)
        (folder / "fixture-worker.json").write_text(json.dumps({
            "agent": "fixture-worker", "state": "online", "source": "isolated-test-fixture",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        }), encoding="utf-8")
        roster = root / "roster.json"
        roster.write_text(json.dumps({"agents": [{"agent": "fixture-worker", "actions": [action]}]}),
                          encoding="utf-8")
        return roster

    def _wait(self, predicate, process, log, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            if process.poll() is not None:
                self.fail(f"watcher exited {process.returncode}: {log.read_text(encoding='utf-8', errors='replace')}")
            time.sleep(0.05)
        self.fail(f"filesystem event did not reconcile within {timeout}s: "
                  f"{log.read_text(encoding='utf-8', errors='replace')}")

    @contextmanager
    def _watch(self, root, roster):
        ready = root / "watcher-ready.json"
        log = root / "watcher.log"
        env = {**os.environ, "PYTHONPATH": str(REPO / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}
        args = [PWSH, "-NoProfile", "-File", str(REPO / "scripts/watch-improvements.ps1"),
                "-Python", sys.executable, "-CoordRoot", str(root), "-RosterFile", str(roster),
                "-ReadyFile", str(ready)]
        with log.open("wb") as output:
            process = subprocess.Popen(args, cwd=REPO, env=env, stdout=output, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                def started():
                    try:
                        return json.loads(ready.read_text(encoding="utf-8"))["status"] == "READY"
                    except (OSError, ValueError, KeyError):
                        return False
                self._wait(started, process, log)
                self.assertEqual(json.loads(ready.read_text(encoding="utf-8"))["pid"], process.pid)
                yield process, log
            finally:
                if process.poll() is None:
                    (root / "improvement_spool" / "inbox" / ".stop").write_text("fixture stop", encoding="utf-8")
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                        self.fail("watcher did not honor its stop marker promptly")

    def _assert_queued(self, root, identity, process, log):
        self._wait(lambda: len(commander._orders(root)) == 1, process, log)
        order = commander._orders(root)[0]
        head = improvement.status(root, identity)["events"][-1]["sha256"]
        self.assertEqual(order["event_id"], "EVT-IMPROVE-" + head[:24].upper())
        self.assertEqual(order["target"], "fixture-worker")
        self.assertEqual(order["state"], "DEPOSITED")
        self.assertFalse(order["allow_launch"])
        self.assertEqual(list((root / "improvement_spool" / "inbox").glob("*.json")), [])

    def test_new_proposal_directory_is_watched_after_startup(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            roster = self._roster(root, "validate")
            with self._watch(root, roster) as (process, log):
                identity, _ = self._proposal(root)
                self._assert_queued(root, identity, process, log)

    def test_stage_advance_without_new_inbox_queues_review_and_deduplicates_events(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            identity, proof = self._proposal(root)
            roster = self._roster(root, "review")
            with self._watch(root, roster) as (process, log):
                self.assertEqual(commander._orders(root), [])
                improvement.advance(root, identity, state="VALIDATED", actor="fixture-tester",
                                    evidence=[str(proof)], note="fixture stage transition")
                self._assert_queued(root, identity, process, log)
                journal = improvement._folder(root, identity) / "000001.json"
                for _ in range(10):
                    os.utime(journal, None)
                time.sleep(1)
                self.assertIsNone(process.poll())
                self.assertEqual(len(commander._orders(root)), 1)
            self.assertEqual(process.returncode, 0)

    def test_claim_release_without_new_inbox_queues_validation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            identity, _ = self._proposal(root)
            lease = improvement_work.claim(root, identity, actor="fixture-holder")
            roster = self._roster(root, "validate")
            with self._watch(root, roster) as (process, log):
                self.assertEqual(commander._orders(root), [])
                improvement_work.finish(root, identity, actor="fixture-holder", token=lease["token"], release=True)
                self._assert_queued(root, identity, process, log)
            self.assertEqual(process.returncode, 0)

    def test_completed_validation_receipt_without_new_inbox_releases_handoff(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            identity, _ = self._proposal(root)
            folder = improvement._folder(root, identity) / "runs" / uuid4().hex
            request = improvement_runs.begin(root, identity, folder,
                head=improvement.status(root, identity)["events"][-1]["sha256"],
                actor="fixture-tester", token=None, command=[sys.executable, "-m", "unittest"],
                workspace=root, timeout=30)
            # Legacy request/receipt fixture; no test process is claimed to have run.
            (folder / "report.json").write_text(json.dumps({"status": "FAILED", "tests_run": 1,
                "exit_code": 1, "fixture_only": True}), encoding="utf-8")
            (folder / "output.txt").write_text("synthetic failed-result fixture", encoding="utf-8")
            roster = self._roster(root, "validate")
            with self._watch(root, roster) as (process, log):
                self.assertEqual(commander._orders(root), [])
                improvement_runs.complete(folder, request, status="FAILED")
                self._assert_queued(root, identity, process, log)
            self.assertEqual(process.returncode, 0)

    def test_output_and_temporary_files_do_not_trigger_scheduler(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            identity, proof = self._proposal(root)
            roster = self._roster(root, "review")
            with self._watch(root, roster) as (process, log):
                with _task_writer_lock(root, "improvement-dispatch"):
                    folder = improvement._folder(root, identity)
                    (folder / "000001.json.fixture.tmp").write_text("not published", encoding="utf-8")
                    (folder / "output.txt").write_text("changing output", encoding="utf-8")
                    (folder / "report.json.fixture.tmp").write_text("temporary report", encoding="utf-8")
                    receipts = root / "improvement_spool" / "receipts"
                    receipts.mkdir(parents=True, exist_ok=True)
                    (receipts / "fixture.json").write_text("{}", encoding="utf-8")
                    time.sleep(1.2)
                    self.assertIsNone(process.poll(), log.read_text(encoding="utf-8", errors="replace"))
                improvement.advance(root, identity, state="VALIDATED", actor="fixture-tester",
                                    evidence=[str(proof)], note="subsequent genuine event")
                self._assert_queued(root, identity, process, log)

    def test_publisher_lock_conflict_stops_without_replaying_after_release(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            identity, proof = self._proposal(root)
            roster = self._roster(root, "review")
            with self._watch(root, roster) as (process, log):
                with _task_writer_lock(root, "improvement-dispatch"):
                    improvement.advance(root, identity, state="VALIDATED", actor="fixture-tester",
                                        evidence=[str(proof)], note="event while writer lock held")
                    process.wait(timeout=12)
                    self.assertNotEqual(process.returncode, 0)
                self.assertEqual(commander._orders(root), [])
                text = log.read_text(encoding="utf-8", errors="replace")
                self.assertIn("Scheduling failed", text)
                self.assertIn("No automatic retry was attempted", text)
                self.assertIn("being written", text)

    def test_inbox_publication_while_locked_stops_without_hidden_retry(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            roster = self._roster(root, "validate")
            with self._watch(root, roster) as (process, log):
                with _task_writer_lock(root, "improvement-spool"):
                    # Simulate the publisher's atomic write before releasing its lock.
                    _atomic_json(root / "improvement_spool" / "inbox" / "fixture.json", {})
                    process.wait(timeout=12)
                    self.assertNotEqual(process.returncode, 0)
                self.assertEqual(commander._orders(root), [])
                self.assertEqual(list((root / "improvement_spool" / "receipts").glob("*.json")), [])
                text = log.read_text(encoding="utf-8", errors="replace")
                self.assertIn("Drain failed", text)
                self.assertIn("No automatic retry was attempted", text)

    def test_second_instance_fails_before_reconciliation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            roster = self._roster(root, "validate")
            with self._watch(root, roster) as (process, log):
                second = subprocess.run([PWSH, "-NoProfile", "-File",
                    str(REPO / "scripts/watch-improvements.ps1"), "-Python", sys.executable,
                    "-CoordRoot", str(root), "-Once"], capture_output=True, text=True,
                    timeout=12, creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertNotEqual(second.returncode, 0)
                self.assertIn("watcher.lock", second.stderr)
                self.assertIsNone(process.poll(), log.read_text(encoding="utf-8", errors="replace"))

    def test_ready_file_preflight_rejects_existing_and_input_paths(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            self._proposal(root)
            roster = self._roster(root, "validate")
            existing = root / "existing.json"
            existing.write_text("preserve", encoding="utf-8")
            inbox = root / "improvement_spool" / "inbox"
            inbox.mkdir(parents=True)
            for path, reason in ((existing, "already exists"), (inbox / "ready.json", "outside authoritative")):
                with self.subTest(path=path):
                    result = subprocess.run([PWSH, "-NoProfile", "-File",
                        str(REPO / "scripts/watch-improvements.ps1"), "-Python", sys.executable,
                        "-CoordRoot", str(root), "-RosterFile", str(roster), "-ReadyFile", str(path),
                        "-Once"], capture_output=True, text=True, timeout=12,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(reason, result.stderr)
                    self.assertEqual(commander._orders(root), [])
            self.assertEqual(existing.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
