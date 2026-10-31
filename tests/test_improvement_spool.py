import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import os
import shutil
import subprocess
import sys
import time
from agent_evolution_protocol.coordination import improvement_spool as spool


def _tempdir():
    """A temp root that survives a scanner holding a just-released lock file.

    These fixtures hand a lock file to a real PowerShell process.  Windows can
    still report a transient sharing violation while an indexer or a
    late-closing child lets go of it, and that says nothing about the watcher.
    A leaked lease is therefore asserted explicitly by ``_assert_lease_released``
    instead of being inferred from whether the directory could be deleted.
    """
    return TemporaryDirectory(ignore_cleanup_errors=True)


def _assert_lease_released(case, root):
    """Prove the watcher's OS-held lease is gone, not just that it exited."""
    lock = Path(root) / "improvement_spool" / "watcher.lock"
    if not lock.exists():
        return
    deadline = time.monotonic() + 10
    while True:
        try:
            with lock.open("r+b"):
                return
        except OSError:
            if time.monotonic() >= deadline:
                case.fail("watcher.lock is still held after the watcher exited")
            time.sleep(0.1)


class SpoolTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32" and shutil.which("pwsh.exe"), "Windows PowerShell 7")
    def test_live_watcher_receives_event_and_stops(self):
        repo = Path(__file__).resolve().parents[1]
        with _tempdir() as temp:
            root = Path(temp)
            args = [shutil.which("pwsh.exe"), "-NoProfile", "-File",
                    str(repo / "scripts/watch-improvements.ps1"), "-Python", sys.executable,
                    "-CoordRoot", temp]
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env={**os.environ, "PYTHONPATH": str(repo / "src")})
            try:
                proof = root / "proof"
                proof.write_text("ok")
                event = root / "source.json"
                event.write_text(json.dumps(dict(event_id="live", kind="TASK_COMPLETED", improvement=dict(
                    author="a", change="b", acceptance="c", rollback="d", scope="e", evidence=[str(proof)]))))
                # Startup or filesystem notification must deliver without the 60s reconciliation.
                spool.enqueue(root, event)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline and spool.overview(root)["queue"]["processed"] != 1:
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
                self.assertEqual(spool.overview(root)["queue"]["processed"], 1)
                stop = subprocess.run(args + ["-Stop"], capture_output=True, timeout=15)
                self.assertEqual(stop.returncode, 0)
                process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0)
                # A watcher that exits but keeps its lease would block every
                # later restart, so release is asserted rather than assumed.
                _assert_lease_released(self, root)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_overview_and_corrupt_receipt_recovery(self):
        with _tempdir() as temp:
            root = Path(temp)
            inbox = root / "improvement_spool" / "inbox"
            inbox.mkdir(parents=True)
            (inbox / "bad.json").write_text("invalid")
            self.assertEqual(spool.overview(root)["queue"]["pending"], 1)
            spool.drain(root)
            receipt = root / "improvement_spool" / "receipts" / "bad.json"
            receipt.write_text("broken receipt")
            self.assertEqual(spool.overview(root)["queue"]["damaged"], 1)
            self.assertEqual(spool.drain(root)["processed"], 1)
            self.assertEqual(spool.overview(root)["queue"]["rejected"], 1)
            self.assertEqual(len(list((root / "improvement_spool" / "damaged_receipts").glob("*.json"))), 1)

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("pwsh.exe"), "Windows PowerShell 7")
    def test_stop_flag_prevents_restart(self):
        repo = Path(__file__).resolve().parents[1]
        with _tempdir() as temp:
            args = [shutil.which("pwsh.exe"), "-NoProfile", "-File",
                    str(repo / "scripts/watch-improvements.ps1"), "-Python", sys.executable,
                    "-CoordRoot", temp]
            stopped = subprocess.run(args + ["-Stop"], capture_output=True, text=True, timeout=30)
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            start = subprocess.run(args + ["-Once"], capture_output=True, text=True, timeout=30)
            self.assertNotEqual(start.returncode, 0)
            self.assertIn("Stop flag is set", start.stderr)

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("pwsh.exe"), "Windows PowerShell 7")
    def test_watcher_once_cleans_up_subscriptions(self):
        repo = Path(__file__).resolve().parents[1]
        with _tempdir() as temp:
            run = subprocess.run([shutil.which("pwsh.exe"), "-NoProfile", "-File",
                str(repo / "scripts/watch-improvements.ps1"), "-Python", sys.executable,
                "-CoordRoot", temp, "-Once"], capture_output=True, text=True, timeout=30,
                env={**os.environ, "PYTHONPATH": str(repo / "src")})
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_delivery_reconciliation_and_replay_guard(self):
        with _tempdir() as temp:
            root = Path(temp)
            proof = root / "proof"
            proof.write_text("ok")
            event = root / "event.json"
            payload = dict(event_id="done-1", kind="TASK_COMPLETED", improvement=dict(
                author="a", change="b", acceptance="c", rollback="d", scope="e", evidence=[str(proof)]))
            event.write_text(json.dumps(payload))
            spool.enqueue(root, event)
            self.assertEqual(spool.drain(root)["results"][0]["status"], "PROPOSED")
            self.assertEqual(spool.drain(root)["processed"], 0)
            receipt = next((root / "improvement_spool" / "receipts").glob("*.json"))
            receipt.unlink()  # Crash after ingest but before acknowledgement is replay-safe.
            self.assertEqual(spool.drain(root)["results"][0]["status"], "PROPOSED")
            payload["kind"] = "TASK_FAILED"
            event.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                spool.enqueue(root, event)

    def test_poison_message_does_not_block_other_work(self):
        with _tempdir() as temp:
            root = Path(temp)
            inbox = root / "improvement_spool" / "inbox"
            inbox.mkdir(parents=True)
            (inbox / "bad.json").write_text("not json")
            result = spool.drain(root)
            self.assertEqual(result["results"][0]["status"], "REJECTED")
            self.assertEqual(spool.drain(root)["processed"], 0)
