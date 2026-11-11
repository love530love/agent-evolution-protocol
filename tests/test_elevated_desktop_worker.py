"""Contracts for the opt-in elevated exact-order desktop worker."""

import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from agent_evolution_protocol.coordination import commander


REPO = Path(__file__).resolve().parents[1]


class ElevatedDesktopWorkerContractTests(unittest.TestCase):
    def test_worker_is_ascii_gated_and_exact_order_only(self):
        worker = REPO / "scripts" / "run-elevated-desktop-order.ps1"
        self.assertTrue(worker.is_file())
        raw = worker.read_bytes()
        self.assertTrue(all(byte < 128 for byte in raw),
                        "PowerShell task entrypoints stay ASCII-compatible")
        text = raw.decode("ascii")

        for token in (
            "[string]$OrderId", "[string]$EventId", "[string]$ReceiptPath",
            "[switch]$Apply", "[switch]$ProbeOnly", "ORDER_ID_MISMATCH", "EVENT_ID_MISMATCH",
            "ORDER_NOT_DEPOSITED", "LAUNCH_FORBIDDEN", "allow_launch",
            "RECEIPT_PATH_MUST_BE_CANONICAL", "RECEIPT_ALREADY_EXISTS",
            "PYTHON_NOT_APPROVED", "APPROVED_AEP_PYTHON_NOT_FOUND",
            "commander\\receipts\\elevated",
            "EXECUTOR_STARTED", "delivery_success   = $false",
            "task_delivered     = $false", "agent_accepted     = $false",
            "client_launch      = $false", "session_created    = $false",
            "commander.dispatch(root, order=order)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh.exe"),
                     "PowerShell task entrypoint behavior is Windows-only")
class ElevatedDesktopWorkerBehaviorTests(unittest.TestCase):
    def test_probe_preview_reads_one_order_and_never_starts_executor(self):
        worker = REPO / "scripts" / "run-elevated-desktop-order.ps1"
        text = worker.read_text(encoding="ascii")
        with TemporaryDirectory() as directory:
            root = Path(directory) / "coordination"
            order = commander.submit_order(
                root, target="qoder", task="ELEVATED_PROBE_PREVIEW_TEST",
                text="no live desktop action", sender="test",
                event_id="EVT-ELEVATED-PROBE-PREVIEW", allow_launch=False,
            )
            receipt = root / "commander" / "receipts" / "elevated" / f"{order['id']}.json"
            completed = subprocess.run(
                ["pwsh.exe", "-NoLogo", "-NoProfile", "-File", str(worker),
                 "-OrderId", order["id"], "-EventId", order["event_id"],
                 "-ReceiptPath", str(receipt), "-CoordRoot", str(root),
                 "-ProbeOnly"],
                cwd=REPO, text=True, encoding="utf-8", capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            value = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(value["state"], "PROBE_PREVIEW")
            self.assertFalse(value["executor_started"])
            self.assertFalse(value["probe_invoked"])
            self.assertFalse(value["commander_invoked"])
            self.assertFalse(value["delivery_success"])
            self.assertFalse(value["client_launch"])
            self.assertFalse(value["session_created"])

        self.assertNotIn("Start-Process", text)
        self.assertNotIn("commander.tick", text)
        self.assertNotIn("--all", text)
        self.assertLess(text.index("if (-not $Apply)"), text.index("& $Python"))
        self.assertLess(text.index("Test-ElevatedInteractiveToken"), text.index("& $Python"))
        self.assertLess(text.index("if bool(order.get(\"allow_launch\"))"),
                        text.index("commander.dispatch(root, order=order)"))

    def test_noncanonical_receipt_is_refused_without_an_out_of_root_write(self):
        worker = REPO / "scripts" / "run-elevated-desktop-order.ps1"
        with TemporaryDirectory() as directory:
            root = Path(directory) / "coordination"
            order = commander.submit_order(
                root, target="qoder", task="ELEVATED_RECEIPT_BOUNDARY_TEST",
                text="no live desktop action", sender="test",
                event_id="EVT-ELEVATED-RECEIPT-BOUNDARY", allow_launch=False,
            )
            outside = Path(directory) / "outside-receipt.json"
            completed = subprocess.run(
                ["pwsh.exe", "-NoLogo", "-NoProfile", "-File", str(worker),
                 "-OrderId", order["id"], "-EventId", order["event_id"],
                 "-ReceiptPath", str(outside), "-CoordRoot", str(root)],
                cwd=REPO, text=True, encoding="utf-8", capture_output=True,
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertFalse(outside.exists())
            self.assertIn("RECEIPT_PATH_MUST_BE_CANONICAL", completed.stdout + completed.stderr)

    def test_probe_only_is_a_separate_read_only_path(self):
        text = (REPO / "scripts" / "run-elevated-desktop-order.ps1").read_text(encoding="ascii")
        # The worker contains an early preview branch too.  Slice the
        # *runtime* ProbeOnly branch through its matching ``else`` rather
        # than accidentally inspecting the later commander bootstrap.
        start = text.index("    if ($ProbeOnly) {\n        # A read-only first contact")
        finish = text.index("    } else {\n        $bootstrap = @'", start)
        probe = text[start:finish]
        for token in (
            "coord-uia", "observe", "--ocr", "PROBE_COMPLETED",
            "AEP_ELEVATED_PROBE=", "probe_invoked",
        ):
            with self.subTest(token=token):
                self.assertIn(token, probe)
        self.assertNotIn("commander.dispatch", probe)
        self.assertNotIn("Start-Process", probe)
        self.assertNotIn("SendKeys", probe)
        self.assertNotIn("SetValue", probe)

    def test_registration_is_explicit_interactive_highest_and_on_demand(self):
        registration = REPO / "scripts" / "register-elevated-desktop-order-task.ps1"
        self.assertTrue(registration.is_file())
        raw = registration.read_bytes()
        self.assertTrue(all(byte < 128 for byte in raw),
                        "PowerShell task entrypoints stay ASCII-compatible")
        text = raw.decode("ascii")

        for token in (
            "[string]$OrderId", "[string]$EventId", "[string]$ReceiptPath",
            "[switch]$Register", "[switch]$ProbeOnly", "if (-not $Register)", "RunLevel Highest",
            "LogonType Interactive", "MultipleInstances IgnoreNew",
            "ExecutionTimeLimit", "New-ScheduledTask -Action",
            "ORDER_NOT_DEPOSITED", "LAUNCH_FORBIDDEN", "Register-ScheduledTask",
            "RECEIPT_PATH_MUST_BE_CANONICAL", "PYTHON_NOT_APPROVED",
            "commander\\receipts\\elevated",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)

        before, after = text.split("if (-not $Register)", 1)
        self.assertNotIn("Register-ScheduledTask", before)
        self.assertIn("Register-ScheduledTask", after)
        self.assertNotIn("Start-ScheduledTask", text)
        self.assertNotIn("Start-Process", text)

    def test_registration_refuses_an_unapproved_python_before_task_creation(self):
        registration = REPO / "scripts" / "register-elevated-desktop-order-task.ps1"
        with TemporaryDirectory() as directory:
            root = Path(directory) / "coordination"
            order = commander.submit_order(
                root, target="qoder", task="ELEVATED_PYTHON_BOUNDARY_TEST",
                text="no live desktop action", sender="test",
                event_id="EVT-ELEVATED-PYTHON-BOUNDARY", allow_launch=False,
            )
            receipt = root / "commander" / "receipts" / "elevated" / f"{order['id']}.json"
            foreign = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
            completed = subprocess.run(
                ["pwsh.exe", "-NoLogo", "-NoProfile", "-File", str(registration),
                 "-OrderId", order["id"], "-EventId", order["event_id"],
                 "-ReceiptPath", str(receipt), "-CoordRoot", str(root),
                 "-Python", str(foreign)],
                cwd=REPO, text=True, encoding="utf-8", capture_output=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("PYTHON_NOT_APPROVED", completed.stdout + completed.stderr)

    def test_document_keeps_executor_receipt_separate_from_delivery_proof(self):
        document = REPO / "docs" / "ELEVATED_DESKTOP_WORKER.zh-CN.md"
        self.assertTrue(document.is_file())
        text = document.read_text(encoding="utf-8")
        for token in (
            "EXECUTOR_STARTED", "delivery_success", "MESSAGE_VISIBLE",
            "AGENT_ACK", "TASK_RESULT_READBACK", "allow_launch: false",
            "Start-ScheduledTask", "no trigger", "PROBE_COMPLETED",
            "coord-uia observe --agent <order target> --ocr",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
