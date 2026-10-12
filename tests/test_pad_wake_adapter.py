"""PAD integration must be buildable without sending a real desktop input."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "src/agent_evolution_protocol/coordination/wake_adapters/pad_flow.ps1"


@unittest.skipUnless(sys.platform == "win32" and shutil.which("pwsh.exe"), "Windows PowerShell 7 required")
class PadWakeAdapterTests(unittest.TestCase):
    def test_build_only_never_dispatches(self):
        with tempfile.TemporaryDirectory() as temp:
            wake = Path(temp) / "test-wake.json"
            wake.write_text(json.dumps({"id": "pad-test-1", "type": "wake_request", "to": "workbuddy", "task": "T136"}), encoding="utf-8")
            run = subprocess.run([
                shutil.which("pwsh.exe"), "-NoProfile", "-File", str(SCRIPT),
                "-WakeFile", str(wake), "-Agent", "workbuddy",
                "-WorkflowId", "11111111-1111-1111-1111-111111111111", "-BuildOnly",
            ], capture_output=True, text=True, timeout=20, check=False)
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads(run.stdout)
            self.assertEqual(report["status"], "pad-url-built-not-run")
            self.assertFalse(report["task_delivered"])
            self.assertFalse(Path(report["receipt_file"]).exists())

    def test_wrong_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            wake = Path(temp) / "test-wake.json"
            wake.write_text(json.dumps({"id": "pad-test-2", "type": "wake_request", "to": "qoder", "task": "T136"}), encoding="utf-8")
            run = subprocess.run([
                shutil.which("pwsh.exe"), "-NoProfile", "-File", str(SCRIPT),
                "-WakeFile", str(wake), "-Agent", "workbuddy",
                "-WorkflowId", "11111111-1111-1111-1111-111111111111", "-BuildOnly",
            ], capture_output=True, text=True, timeout=20, check=False)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("PAD_EVENT_MISMATCH", run.stderr)
