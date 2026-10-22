import base64
import csv
import hashlib
import io
import json
from pathlib import Path
from contextlib import redirect_stdout
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_evolution_protocol.coordination import dependency_inventory as inventory


class DependencyInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.environment = self.root / "sdk"
        self.site = self.environment / "Lib/site-packages"
        self.site.mkdir(parents=True)
        (self.environment / "Scripts").mkdir()
        (self.environment / "Scripts/python.exe").write_bytes(b"not executable")
        (self.environment / "pyvenv.cfg").write_text("home = nonexistent\n")
        self.info = self.distribution("mcp", "1.30.0")
        self.code = self.site / "mcp.py"
        self.code.write_text("raise RuntimeError('MUST NEVER EXECUTE')\n")
        self.write_record()
        self.lock = self.root / "lock.json"
        self.lock.write_text(json.dumps({"schema": 1, "profile": "desktop-win-cp313",
            "wheels": [{"name": "mcp", "version": "1.30.0"}]}))
        self.output = self.root / "report"

    def distribution(self, name, version, directory=None):
        info = self.site / (directory or f"{name}-{version}.dist-info")
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n")
        return info

    def write_record(self, extra=None):
        with (self.info / "RECORD").open("w", newline="") as stream:
            writer = csv.writer(stream)
            for path in (self.code, self.info / "METADATA"):
                body = path.read_bytes()
                digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip("=")
                writer.writerow([path.relative_to(self.site).as_posix(), "sha256=" + digest, len(body)])
            writer.writerow(["mcp-1.30.0.dist-info/RECORD", "", ""])
            if extra:
                writer.writerow(extra)

    def inspect(self, **kwargs):
        return inventory.inspect(lock=self.lock, environment=self.environment, output=self.output, **kwargs)

    def report(self):
        return json.loads((self.output / "inventory.json").read_text())

    def test_plan_no_io_or_interpreter(self):
        result = inventory.inspect(lock=self.root / "missing", environment=self.root / "missing-sdk", output=self.output)
        self.assertEqual(result["status"], "PLAN_ONLY")
        self.assertFalse(self.output.exists())

    def test_report_cannot_write_into_inspected_environment(self):
        output = self.environment / "new-report"
        with self.assertRaises(ValueError):
            inventory.inspect(lock=self.lock, environment=self.environment, output=output, apply=True)
        self.assertFalse(output.exists())

    def test_matches_records_without_running_python_or_startup_hook(self):
        (self.site / "sitecustomize.py").write_text("raise RuntimeError('MUST NEVER EXECUTE')\n")
        result = self.inspect(apply=True, verify_records=True)
        self.assertEqual(result["status"], "LOCK_VERSIONS_AND_RECORDS_MATCH")
        self.assertEqual(result["checked_files"], 2)
        self.assertFalse(self.report()["interpreter_started"])
        self.assertFalse(self.report()["startup_files"][0]["executed"])
        self.assertFalse(self.report()["artifact_integrity_verified_against_wheels"])
        self.assertEqual(self.report()["lock_sha256"], hashlib.sha256(self.lock.read_bytes()).hexdigest())

    def test_changed_file_rejected_and_never_repaired(self):
        self.code.write_text("changed")
        self.assertEqual(self.inspect(apply=True, verify_records=True)["status"], "RECORD_CHECK_FAILED")
        self.assertEqual(self.code.read_text(), "changed")

    def test_missing_version_extra_and_bootstrap_are_distinguished(self):
        (self.info / "METADATA").write_text("Name: mcp\nVersion: 1.29.0\n")
        self.distribution("unexpected", "1.0")
        self.distribution("pip", "26.1")
        result = self.inspect(apply=True)
        self.assertEqual(result["status"], "ENVIRONMENT_MISMATCH")
        self.assertEqual(result["version_mismatch_count"], 1)
        self.assertEqual(self.report()["unexpected_packages"], ["unexpected"])
        self.assertEqual(self.report()["bootstrap_packages_outside_lock"][0]["name"], "pip")

    def test_duplicate_identity_and_missing_package_fail_closed(self):
        self.distribution("mcp", "1.30.0", directory="duplicate.dist-info")
        self.assertEqual(self.inspect(apply=True)["status"], "ENVIRONMENT_MISMATCH")
        self.assertTrue(self.report()["metadata_errors"])

    def test_missing_package_and_missing_record_are_distinct(self):
        manifest = json.loads(self.lock.read_text())
        manifest["wheels"].append({"name": "missing", "version": "1.0"})
        self.lock.write_text(json.dumps(manifest))
        result = self.inspect(apply=True)
        self.assertEqual(result["status"], "ENVIRONMENT_MISMATCH")
        self.assertEqual(result["missing_count"], 1)
        manifest["wheels"].pop()
        self.lock.write_text(json.dumps(manifest))
        (self.info / "RECORD").unlink()
        result = inventory.inspect(lock=self.lock, environment=self.environment, output=self.root / "missing-record", apply=True, verify_records=True)
        self.assertEqual(result["status"], "RECORD_CHECK_FAILED")

    def test_scripts_inside_environment_are_valid_record_targets(self):
        script = self.environment / "Scripts/tool.exe"
        script.write_bytes(b"not executable")
        digest = base64.urlsafe_b64encode(hashlib.sha256(script.read_bytes()).digest()).decode().rstrip("=")
        self.write_record(["../../Scripts/tool.exe", "sha256=" + digest, script.stat().st_size])
        result = self.inspect(apply=True, verify_records=True)
        self.assertEqual(result["status"], "LOCK_VERSIONS_AND_RECORDS_MATCH")
        self.assertEqual(result["checked_files"], 3)

    def test_escape_is_never_hashed_and_empty_record_fails(self):
        outside = self.root / "outside.txt"
        outside.write_text("secret")
        self.write_record(["../../../outside.txt", "sha256=AAAA", "6"])
        original = inventory._hash
        with patch.object(inventory, "_hash", wraps=original) as hashed:
            result = self.inspect(apply=True, verify_records=True)
        self.assertEqual(result["status"], "RECORD_CHECK_FAILED")
        self.assertNotIn(outside.resolve(), [call.args[0] for call in hashed.call_args_list])
        (self.info / "RECORD").write_text("")
        result = inventory.inspect(lock=self.lock, environment=self.environment, output=self.root / "empty-record", apply=True, verify_records=True)
        self.assertEqual(result["status"], "RECORD_CHECK_FAILED")

    def test_unhashed_cache_is_optional_but_unhashed_code_is_not(self):
        self.write_record(["mcp.pyc", "", ""])
        self.assertEqual(self.inspect(apply=True, verify_records=True)["status"], "LOCK_VERSIONS_AND_RECORDS_MATCH")
        self.write_record(["unhashed.py", "", ""])
        result = inventory.inspect(lock=self.lock, environment=self.environment, output=self.root / "unhashed-code", apply=True, verify_records=True)
        self.assertEqual(result["status"], "RECORD_CHECK_FAILED")

    def test_no_overwrite_and_cli_mismatch_exit_two(self):
        self.inspect(apply=True)
        with self.assertRaises(ValueError):
            self.inspect(apply=True)
        from agent_evolution_protocol.cli import coord_dependency_inventory
        (self.info / "METADATA").write_text("Name: mcp\nVersion: 0.0\n")
        args = SimpleNamespace(lock=self.lock, environment=self.environment, output=self.root / "cli", apply=True, verify_records=False)
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            coord_dependency_inventory(args)
        self.assertEqual(stopped.exception.code, 2)
