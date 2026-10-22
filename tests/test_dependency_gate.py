from datetime import datetime, timedelta, timezone
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from types import SimpleNamespace

from agent_evolution_protocol.coordination import dependency_gate as gate
from agent_evolution_protocol.coordination.dependency_audit import ENDPOINT


class DependencyGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lock = self.root / "lock.json"
        self.lock.write_text(json.dumps({"schema": 1, "profile": "desktop-win-cp313", "wheels": [{"name": "mcp", "version": "1.30.0"}]}))
        self.digest = hashlib.sha256(self.lock.read_bytes()).hexdigest()
        self.now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
        common = {"schema": 1, "at": self.now.isoformat(), "lock_sha256": self.digest}
        installed = {**common, "status": "LOCK_VERSIONS_AND_RECORDS_MATCH", "environment": "C:/sdk",
            "installed": {"mcp": "1.30.0", "pip": "25.2"}, "missing": [], "version_mismatches": [],
            "unexpected_packages": [], "metadata_errors": [], "lock_versions_match": True,
            "record_hashes_match": True, "checked_files": 2,
            "records": [{"package": "mcp-1.30.0.dist-info", "issues": [], "checked_files": 2}]}
        self.directories = {"audit": self.root / "audit", "inventory": self.root / "inventory",
                            "wheel": self.root / "wheel", "wheel_inventory": self.root / "wheel/inventory"}
        self.names = {"audit": "audit.json", "inventory": "inventory.json",
                      "wheel": "wheel-comparison.json", "wheel_inventory": "inventory.json"}
        self.data = {
            "audit": {**common, "status": "NO_KNOWN_VULNERABILITIES", "source": ENDPOINT,
                "query_attempted": True, "complete": True, "finding_id_count": 0,
                "packages": [{"name": "mcp", "version": "1.30.0", "status": "NO_KNOWN_VULNERABILITIES", "ids": [], "complete": True}]},
            "inventory": installed,
            "wheel": {**common, "status": "WHEEL_PAYLOADS_MATCH", "wheel_payloads_match": True,
                "environment": "C:/sdk", "checked_files": 2,
                "packages": [{"name": "mcp", "wheel_sha256_matches": True, "issues": [], "unsupported": [], "checked_files": 2}]},
            "wheel_inventory": {**copy.deepcopy(installed), "status": "LOCK_VERSIONS_MATCH", "records": [], "record_hashes_match": False, "checked_files": 0}}
        self.raw = {"query.json": {"queries": [{"package": {"ecosystem": "PyPI", "name": "mcp"}, "version": "1.30.0"}]},
                    "response.json": {"results": [{}]}}
        self.write("audit")
        self.write("inventory")
        digest = self.write("wheel_inventory")
        self.data["wheel"]["inventory_sha256"] = digest
        self.write("wheel")
        self.output = self.root / "gate"

    def write(self, label):
        directory = self.directories[label]
        directory.mkdir(parents=True, exist_ok=True)
        values = {self.names[label]: self.data[label]}
        if label == "audit":
            values.update(self.raw)
        checksums = {}
        for name, value in values.items():
            body = (json.dumps(value, indent=2) + "\n").encode()
            (directory / name).write_bytes(body)
            checksums[name] = hashlib.sha256(body).hexdigest()
        (directory / "checksums.json").write_text(json.dumps(checksums))
        return checksums[self.names[label]]

    def evaluate(self, **kwargs):
        return gate.evaluate(lock=self.lock, audit_dir=self.directories["audit"],
            inventory_dir=self.directories["inventory"], wheel_dir=self.directories["wheel"],
            output=self.output, now=self.now, **kwargs)

    def test_plan_no_read_or_write(self):
        self.lock.unlink()
        self.assertEqual(self.evaluate()["status"], "PLAN_ONLY")
        self.assertFalse(self.output.exists())

    def test_consistent_evidence_is_not_deployment_authorization(self):
        result = self.evaluate(apply=True)
        self.assertEqual(result["status"], "SCOPED_EVIDENCE_CONSISTENT")
        self.assertFalse(result["deployment_authorized"])
        report = json.loads((self.output / "gate.json").read_text())
        self.assertFalse(report["identity_authenticated"])
        self.assertEqual(len(report["sources"]), 4)
        self.assertTrue(report["excluded_scope"])
        with self.assertRaises(ValueError):
            self.evaluate(apply=True)

    def test_tampered_report_blocks_before_semantic_approval(self):
        (self.directories["audit"] / "audit.json").write_text("{}")
        self.assertEqual(self.evaluate(apply=True)["status"], "EVIDENCE_BLOCKED")

    def test_expired_future_and_naive_timestamps_block(self):
        for stamp in ((self.now - timedelta(hours=25)).isoformat(), (self.now + timedelta(hours=1)).isoformat(), "2026-10-23T12:00:00"):
            self.data["audit"]["at"] = stamp
            self.write("audit")
            output = self.root / ("clock-" + str(len(list(self.root.iterdir()))))
            result = gate.evaluate(lock=self.lock, audit_dir=self.directories["audit"], inventory_dir=self.directories["inventory"], wheel_dir=self.directories["wheel"], output=output, now=self.now, apply=True)
            self.assertEqual(result["status"], "EVIDENCE_BLOCKED")

    def test_other_lock_and_other_environment_block(self):
        self.data["wheel"]["lock_sha256"] = "0" * 64
        self.write("wheel")
        self.assertEqual(self.evaluate(apply=True)["status"], "EVIDENCE_BLOCKED")

    def test_raw_osv_findings_cannot_be_hidden_by_green_summary(self):
        self.raw["response.json"] = {"results": [{"vulns": [{"id": "GHSA-example"}]}]}
        self.write("audit")
        self.assertEqual(self.evaluate(apply=True)["status"], "EVIDENCE_BLOCKED")

    def test_unsupported_wheel_mapping_blocks(self):
        self.data["wheel"]["packages"][0]["unsupported"] = ["headers/example.h"]
        self.write("wheel")
        self.assertEqual(self.evaluate(apply=True)["status"], "EVIDENCE_BLOCKED")

    def test_preflight_snapshot_cannot_be_swapped(self):
        self.data["wheel_inventory"]["environment"] = "C:/different-sdk"
        self.write("wheel_inventory")
        self.assertEqual(self.evaluate(apply=True)["status"], "EVIDENCE_BLOCKED")

    def test_same_lock_other_environment_blocks_even_with_updated_binding(self):
        self.data["wheel_inventory"]["environment"] = "C:/different-sdk"
        self.data["wheel"]["environment"] = "C:/different-sdk"
        self.data["wheel"]["inventory_sha256"] = self.write("wheel_inventory")
        self.write("wheel")
        self.assertEqual(self.evaluate(apply=True)["status"], "EVIDENCE_BLOCKED")

    def test_zero_record_coverage_cannot_be_hidden_by_green_status(self):
        self.data["inventory"]["records"][0]["checked_files"] = 0
        self.data["inventory"]["checked_files"] = 0
        self.write("inventory")
        self.assertEqual(self.evaluate(apply=True)["status"], "EVIDENCE_BLOCKED")

    def test_cli_blocked_evidence_exits_two(self):
        from agent_evolution_protocol.cli import coord_dependency_gate
        (self.directories["audit"] / "response.json").write_text("{}")
        args = SimpleNamespace(lock=self.lock, audit_dir=self.directories["audit"],
            inventory_dir=self.directories["inventory"], wheel_dir=self.directories["wheel"],
            output=self.output, apply=True, max_age_hours=24)
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            coord_dependency_gate(args)
        self.assertEqual(stopped.exception.code, 2)

    def test_unsafe_checksum_names_and_nested_output_rejected(self):
        index = self.directories["inventory"] / "checksums.json"
        index.write_text(json.dumps({"../outside.json": "0" * 64, "inventory.json": "0" * 64}))
        self.assertEqual(self.evaluate(apply=True)["status"], "EVIDENCE_BLOCKED")
        with self.assertRaises(ValueError):
            gate.evaluate(lock=self.lock, audit_dir=self.directories["audit"], inventory_dir=self.directories["inventory"], wheel_dir=self.directories["wheel"], output=self.directories["audit"] / "new-gate", apply=True)

    def test_invalid_expiry_policy_rejected(self):
        for value in (0, 169, True):
            with self.assertRaises(ValueError):
                self.evaluate(apply=True, max_age_hours=value)
