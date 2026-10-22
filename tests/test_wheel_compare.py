import base64
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from agent_evolution_protocol.coordination import wheel_compare


class WheelCompareTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment = self.root / "sdk"
        self.site = self.environment / "Lib/site-packages"
        self.site.mkdir(parents=True)
        (self.environment / "Scripts").mkdir()
        (self.environment / "Scripts/python.exe").write_bytes(b"never execute this")
        (self.environment / "pyvenv.cfg").write_text("home = nonexistent")
        self.wheels = self.root / "wheels"
        self.wheels.mkdir()
        self.wheel = self.wheels / "mcp-1.30.0-py3-none-any.whl"
        self.lock = self.root / "lock.json"
        self.output = self.root / "report"
        self.entries = {"mcp.py": b"raise RuntimeError('MUST NEVER EXECUTE')\n",
            "mcp-1.30.0.dist-info/METADATA": b"Name: mcp\nVersion: 1.30.0\n",
            "mcp-1.30.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\n",
            "mcp-1.30.0.dist-info/RECORD": b"mcp-1.30.0.dist-info/RECORD,,\n"}
        for name, body in self.entries.items():
            path = self.site / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        self.archive()

    def archive(self, extra=None):
        with zipfile.ZipFile(self.wheel, "w") as archive:
            for name, body in {**self.entries, **(extra or {})}.items():
                archive.writestr(name, body)
        self.lock.write_text(json.dumps({"schema": 1, "profile": "desktop-win-cp313", "wheels": [
            {"name": "mcp", "version": "1.30.0", "filename": self.wheel.name,
             "sha256": hashlib.sha256(self.wheel.read_bytes()).hexdigest()}]}))

    def compare(self, **kwargs):
        return wheel_compare.compare(lock=self.lock, wheelhouse=self.wheels,
            environment=self.environment, output=self.output, **kwargs)

    def report(self):
        return json.loads((self.output / "wheel-comparison.json").read_text())

    def test_preview_no_read_write_or_execution(self):
        self.lock.unlink()
        self.assertEqual(self.compare()["status"], "PLAN_ONLY")
        self.assertFalse(self.output.exists())

    def test_exact_payload_ignores_only_mutable_installed_record(self):
        (self.site / "mcp-1.30.0.dist-info/RECORD").write_text("installer rewrote this")
        result = self.compare(apply=True)
        self.assertEqual(result["status"], "WHEEL_PAYLOADS_MATCH")
        self.assertEqual(result["checked_files"], 3)
        self.assertFalse(self.report()["installed_record_trusted"])
        self.assertFalse(self.report()["security_verified"])
        with self.assertRaises(ValueError):
            self.compare(apply=True)

    def test_changed_code_and_forged_record_cannot_hide_difference(self):
        (self.site / "mcp.py").write_text("changed code")
        with (self.site / "mcp-1.30.0.dist-info/RECORD").open("w", newline="") as stream:
            writer = csv.writer(stream)
            for name in self.entries:
                if name.endswith("/RECORD"):
                    writer.writerow([name, "", ""])
                else:
                    body = (self.site / name).read_bytes()
                    digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip("=")
                    writer.writerow([name, "sha256=" + digest, len(body)])
        local = wheel_compare.inventory.inspect(lock=self.lock, environment=self.environment,
            output=self.root / "forged-record-check", apply=True, verify_records=True)
        self.assertEqual(local["status"], "LOCK_VERSIONS_AND_RECORDS_MATCH")
        self.assertEqual(self.compare(apply=True)["status"], "WHEEL_COMPARISON_FAILED")
        self.assertEqual(self.report()["packages"][0]["issues"][0]["reason"], "BYTES_DIFFER")
        self.assertEqual((self.site / "mcp.py").read_text(), "changed code")

    def test_archive_digest_rejected_before_opening_zip(self):
        manifest = json.loads(self.lock.read_text())
        manifest["wheels"][0]["sha256"] = "0" * 64
        self.lock.write_text(json.dumps(manifest))
        with patch.object(wheel_compare.zipfile, "ZipFile") as opened:
            self.assertEqual(self.compare(apply=True)["status"], "WHEEL_COMPARISON_FAILED")
            opened.assert_not_called()

    def test_escape_and_case_collisions_fail_closed(self):
        self.archive({"../outside.py": b"evil"})
        self.assertEqual(self.compare(apply=True)["status"], "WHEEL_COMPARISON_FAILED")
        self.assertFalse((self.environment / "outside.py").exists())
        self.archive({"MCP.py": b"duplicate Windows destination"})
        result = wheel_compare.compare(lock=self.lock, wheelhouse=self.wheels,
            environment=self.environment, output=self.root / "collision", apply=True)
        self.assertEqual(result["status"], "WHEEL_COMPARISON_FAILED")

    def test_data_scripts_map_inside_environment(self):
        self.archive({"mcp-1.30.0.data/scripts/helper.py": b"# static script\n"})
        (self.environment / "Scripts/helper.py").write_bytes(b"# static script\n")
        self.assertEqual(self.compare(apply=True)["status"], "WHEEL_PAYLOADS_MATCH")

    def test_transformed_or_unsupported_members_never_silently_pass(self):
        self.archive({"mcp-1.30.0.data/scripts/helper.py": b"#!python\npass\n",
                      "mcp-1.30.0.data/headers/header.h": b"unsupported"})
        result = self.compare(apply=True)
        self.assertEqual(result["status"], "INCOMPLETE_MAPPING")
        self.assertEqual(len(self.report()["packages"][0]["unsupported"]), 2)

    def test_missing_file_and_report_inside_sdk_rejected(self):
        (self.site / "mcp.py").unlink()
        self.assertEqual(self.compare(apply=True)["status"], "WHEEL_COMPARISON_FAILED")
        with self.assertRaises(ValueError):
            wheel_compare.compare(lock=self.lock, wheelhouse=self.wheels, environment=self.environment,
                                  output=self.environment / "report", apply=True)
        self.assertFalse((self.environment / "report").exists())
