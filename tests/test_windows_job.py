"""Real, temporary Windows process containment checks; no production jobs."""

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from agent_evolution_protocol.coordination import windows_job


WORKER = Path(windows_job.__file__).with_name("validation_worker.py").resolve()


def _signed(value):
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {**value, "sha256": digest}


@unittest.skipUnless(os.name == "nt", "Windows Job Object integration")
class WindowsJobTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.workspace = self.folder / "workspace"
        (self.workspace / "tests").mkdir(parents=True)
        self.run_folder = self.folder / uuid4().hex
        self.run_folder.mkdir()
        self.request_path = self.run_folder / "request.json"
        self.name = "AEPValidation-" + uuid4().hex
        self.job = windows_job.Job.create(self.name)
        self.addCleanup(self.job.close)
        self.addCleanup(self._terminate_open_job)
        self.request = {
            "schema_version": 1, "id": "a" * 24, "run_id": self.run_folder.name,
            "command": [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            "workspace": str(self.workspace),
        }
        self.execution = {
            "schema_version": 1, "id": self.request["id"], "run_id": self.run_folder.name,
            "backend": "windows-job", "job_name": self.name,
            "request_sha256": _signed(self.request)["sha256"],
            "context": windows_job.execution_context(),
        }
        self._save_records()
        self._fixture("        Path('started.txt').write_text('started')\n")

    def _terminate_open_job(self):
        if self.job._handle is not None:
            self.job.terminate()

    def _save_records(self):
        self.request_path.write_text(json.dumps(_signed(self.request)), encoding="utf-8")
        self.request_path.with_name("execution.json").write_text(
            json.dumps(_signed(self.execution)), encoding="utf-8")

    def _fixture(self, body):
        (self.workspace / "tests" / "test_fixture.py").write_text(
            "import unittest, subprocess, sys, time, os\nfrom pathlib import Path\n"
            "class Fixture(unittest.TestCase):\n    def test_run(self):\n" + body,
            encoding="utf-8")

    def _command(self):
        return [sys.executable, "-I", "-S", str(WORKER), "--job", self.name,
                "--request", str(self.request_path)]

    def _run(self):
        return subprocess.run(self._command(), cwd=self.workspace, capture_output=True,
                              text=True, timeout=10, check=False)

    def _wait_until(self, predicate, message):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail(message)

    def _start_tree(self):
        self._fixture(
            "        child = subprocess.Popen([sys.executable, '-I', '-S', '-c', "
            "\"import time; time.sleep(60)\"])\n"
            "        Path('descendant.txt').write_text(str(child.pid))\n"
            "        time.sleep(60)\n")
        worker = subprocess.Popen(self._command(), cwd=self.workspace,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self._reap_worker, worker)
        marker = self.workspace / "descendant.txt"
        self._wait_until(lambda: marker.exists() and marker.stat().st_size > 0,
                         "contained descendant did not start")
        self._wait_until(lambda: self.job.active_processes() >= 3,
                         "worker, test process and descendant were not contained")
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        # Open only SYNCHRONIZE on the exact child just created by this fixture.
        descendant = api.OpenProcess(0x00100000, False, int(marker.read_text()))
        self.assertTrue(descendant, "could not observe the fixture descendant")
        self.addCleanup(api.CloseHandle, descendant)
        return worker, api, descendant

    @staticmethod
    def _reap_worker(worker):
        if worker.poll() is None:
            worker.terminate()  # Owned Popen handle, never a discovered PID.
        worker.wait(timeout=10)

    def test_job_has_kill_on_close_without_breakaway_or_inheritable_handle(self):
        self.assertEqual(self.job.limit_flags(), windows_job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        self.assertEqual(self.job.active_processes(), 0)
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.GetHandleInformation.restype = wintypes.BOOL
        flags = wintypes.DWORD()
        self.assertTrue(api.GetHandleInformation(self.job._handle, ctypes.byref(flags)))
        self.assertEqual(flags.value & 1, 0)

    def test_duplicate_create_does_not_replace_job_and_open_never_creates(self):
        with self.assertRaises(FileExistsError):
            windows_job.Job.create(self.name)
        opened = windows_job.Job.open(self.name)
        self.assertIsNotNone(opened)
        with opened:
            self.assertEqual(opened.limit_flags(), windows_job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        self.assertIsNone(windows_job.Job.open("AEPValidation-" + uuid4().hex))
        with self.assertRaises(ValueError):
            windows_job.Job.open("some-unrelated-job")

    def test_open_access_denied_is_not_absence(self):
        with patch.object(windows_job, "_api") as factory:
            factory.return_value.OpenJobObjectW.return_value = 0
            with patch.object(ctypes, "get_last_error", return_value=5):
                with self.assertRaises(OSError):
                    windows_job.Job.open(self.name)

    def test_worker_executes_fixed_discovery_in_existing_job(self):
        completed = self._run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Ran 1 test", completed.stderr)
        self.assertTrue((self.workspace / "started.txt").exists())
        self._wait_until(lambda: self.job.active_processes() == 0, "finished worker stayed active")

    def test_delayed_worker_after_parent_close_cannot_start_tests(self):
        self.job.close()
        completed = self._run()
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn("no longer exists", completed.stderr)
        self.assertFalse((self.workspace / "started.txt").exists())
        self.assertIsNone(windows_job.Job.open(self.name))

    def test_worker_refuses_changed_binding_command_context_and_corrupt_hash(self):
        original_request, original_execution = dict(self.request), dict(self.execution)
        changes = (
            ("request_sha256", "0" * 64),
            ("job_name", "AEPValidation-" + uuid4().hex),
            ("context", {"machine": "0" * 64, "session_id": 0}),
        )
        for key, value in changes:
            with self.subTest(field=key):
                self.execution = {**original_execution, key: value}
                self._save_records()
                self.assertEqual(self._run().returncode, 2)
                self.assertFalse((self.workspace / "started.txt").exists())
        self.request = {**original_request, "command": [sys.executable, "-c", "print('unexpected')"]}
        self.execution = {**original_execution, "request_sha256": _signed(self.request)["sha256"]}
        self._save_records()
        self.assertEqual(self._run().returncode, 2)
        self.request_path.write_text('{"sha256": "corrupt"}', encoding="utf-8")
        self.assertEqual(self._run().returncode, 2)
        self.assertEqual(self.job.active_processes(), 0)

    def test_terminate_stops_worker_test_process_and_descendant(self):
        worker, api, descendant = self._start_tree()
        self.job.terminate()
        self.assertEqual(api.WaitForSingleObject(descendant, 8000), 0)
        worker.wait(timeout=8)
        self._wait_until(lambda: self.job.active_processes() == 0, "terminated job still has active members")

    def test_last_parent_handle_close_stops_worker_and_descendant(self):
        worker, api, descendant = self._start_tree()
        self.job.close()
        self.assertEqual(api.WaitForSingleObject(descendant, 8000), 0)
        worker.wait(timeout=8)
        self.assertIsNone(windows_job.Job.open(self.name))

    def test_contained_test_cannot_request_breakaway(self):
        self._fixture(
            "        try:\n"
            "            child = subprocess.Popen([sys.executable, '-I', '-S', '-c', "
            "\"import time; time.sleep(30)\"], creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB)\n"
            "        except OSError:\n"
            "            Path('breakaway-denied.txt').write_text('denied')\n"
            "        else:\n"
            "            child.terminate()\n            child.wait(timeout=5)\n"
            "            self.fail('child unexpectedly escaped its validation job')\n")
        completed = self._run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((self.workspace / "breakaway-denied.txt").exists())

    def test_bootstrap_does_not_import_workspace_job_module(self):
        (self.workspace / "windows_job.py").write_text(
            "from pathlib import Path\nPath('untrusted-bootstrap.txt').write_text('loaded')\n",
            encoding="utf-8")
        completed = self._run()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse((self.workspace / "untrusted-bootstrap.txt").exists())


if __name__ == "__main__":
    unittest.main()
