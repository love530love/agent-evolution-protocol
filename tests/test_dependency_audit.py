import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_evolution_protocol.coordination import dependency_audit as audit


class DependencyAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lock = self.root / "lock.json"
        self.manifest = {"schema": 1, "profile": "desktop-win-cp313", "wheels": [
            {"name": "mcp", "version": "1.30.0"}, {"name": "example", "version": "1.0"}]}
        self.lock.write_text(json.dumps(self.manifest))
        self.output = self.root / "audit"

    def run_audit(self, **kwargs):
        return audit.audit(lock=self.lock, output=self.output, **kwargs)

    def report(self):
        return json.loads((self.output / "audit.json").read_text())

    def test_plan_does_not_read_lock_write_or_query(self):
        self.lock.unlink()
        with patch.object(audit, "_query") as query:
            result = self.run_audit(query_osv=True)
            query.assert_not_called()
        self.assertEqual(result["status"], "PLAN_ONLY")
        self.assertFalse(self.output.exists())

    def test_not_queried_is_explicit_not_success(self):
        with patch.object(audit, "_query") as query:
            result = self.run_audit(apply=True)
            query.assert_not_called()
        self.assertEqual(result["status"], "NOT_QUERIED")
        self.assertFalse(result["complete"])
        self.assertFalse(self.report()["query_attempted"])

    def test_known_ids_are_preserved_and_never_remediated(self):
        captured = []
        def query(payload):
            captured.append(payload)
            return {"results": [{"vulns": [{"id": "GHSA-test-1234"}, {"id": "GHSA-test-1234"}]}, {}]}
        result = self.run_audit(apply=True, query_osv=True, query=query)
        self.assertEqual(result["status"], "KNOWN_VULNERABILITIES")
        self.assertEqual(result["finding_id_count"], 1)
        self.assertEqual(captured[0]["queries"][0], {"package": {"ecosystem": "PyPI", "name": "example"}, "version": "1.0"})
        self.assertFalse(self.report()["remediation_performed"])
        self.assertEqual(self.report()["lock_sha256"], hashlib.sha256(self.lock.read_bytes()).hexdigest())
        checksums = json.loads((self.output / "checksums.json").read_text())
        for name, digest in checksums.items():
            self.assertEqual(digest, hashlib.sha256((self.output / name).read_bytes()).hexdigest())

    def test_empty_findings_are_not_security_certificate(self):
        result = self.run_audit(apply=True, query_osv=True, query=lambda _: {"results": [{}, {"vulns": []}]})
        self.assertEqual(result["status"], "NO_KNOWN_VULNERABILITIES")
        self.assertTrue(result["complete"])
        self.assertFalse(result["security_verified"])
        self.assertFalse(self.report()["artifact_integrity_verified"])

    def test_timeout_is_persisted_not_retried(self):
        with patch.object(audit, "_query", side_effect=TimeoutError("private diagnostic")) as query:
            result = self.run_audit(apply=True, query_osv=True)
            self.assertEqual(query.call_count, 1)
        self.assertEqual(result["status"], "QUERY_FAILED")
        self.assertEqual(self.report()["error"], "TimeoutError")
        self.assertNotIn("private diagnostic", (self.output / "audit.json").read_text())
        with patch.object(audit, "_query") as query, self.assertRaises(ValueError):
            self.run_audit(apply=True, query_osv=True)
        query.assert_not_called()

    def test_pagination_and_individual_errors_are_incomplete(self):
        result = self.run_audit(apply=True, query_osv=True, query=lambda _: {"results": [
            {"vulns": [{"id": "PYSEC-2026-1"}], "next_page_token": "more"}, {"error": "unavailable"}]})
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertFalse(result["complete"])
        self.assertEqual(result["finding_id_count"], 1)
        self.assertEqual(self.report()["packages"][1]["status"], "QUERY_FAILED")

    def test_malformed_response_never_becomes_empty_success(self):
        for response in ({}, {"results": []}, {"results": [None, {}]},
                         {"results": [{"vulns": "bad"}, {}]},
                         {"results": [{"vulns": [{"id": "bad\nidentity"}]}, {}]}):
            with self.subTest(response=response):
                output = self.root / ("case-" + str(len(list(self.root.iterdir()))))
                result = audit.audit(lock=self.lock, output=output, apply=True, query_osv=True, query=lambda _: response)
                self.assertEqual(result["status"], "QUERY_FAILED")

    def test_invalid_or_duplicate_identity_never_queries(self):
        self.manifest["wheels"].append(self.manifest["wheels"][0].copy())
        self.lock.write_text(json.dumps(self.manifest))
        with patch.object(audit, "_query") as query, self.assertRaises(ValueError):
            self.run_audit(apply=True, query_osv=True)
        query.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_cli_unknown_incomplete_and_findings_exit_nonzero(self):
        from agent_evolution_protocol.cli import coord_dependency_audit
        for index, response in enumerate(({}, {"results": [{"next_page_token": "more"}, {}]},
                                          {"results": [{"vulns": [{"id": "GHSA-example"}]}, {}]})):
            arguments = SimpleNamespace(lock=self.lock, output=self.root / f"cli-{index}", apply=True, query_osv=True)
            with self.subTest(response=response), patch.object(audit, "_query", return_value=response), redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped:
                coord_dependency_audit(arguments)
            self.assertEqual(stopped.exception.code, 2)
