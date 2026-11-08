"""Acceptance gate for the OFFLINE delivery-receipt acceptance fixtures.

Four real-machine cases are turned into recomputable, dependency-free
fixtures. The rule borrowed from upstream acceptance practice (idea only,
independently implemented): a check that declares ``requiredEvidence``
cannot pass on prose alone -- a missing artifact marks the round ``uncertain``
and holds the delivery. Nothing here starts an adapter, opens a socket,
writes state, or logs in anywhere.
"""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import unittest

from agent_evolution_protocol.coordination import delivery_acceptance_fixture as daf

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "agent_evolution_protocol" / "coordination" / "delivery_acceptance_fixture.py"


class FixtureCatalogTests(unittest.TestCase):
    def test_four_real_machine_cases_are_registered(self):
        names = [f.name for f in daf.FIXTURES]
        self.assertEqual(names, ["ocr_band_false_negative",
                                 "boolean_false_positive",
                                 "primary_kept_under_conflict",
                                 "submitted_unconfirmed"])

    def test_every_fixture_names_its_source_and_required_evidence(self):
        for fixture in daf.FIXTURES:
            with self.subTest(name=fixture.name):
                self.assertTrue(fixture.source)
                self.assertTrue(fixture.required_evidence)
                self.assertIn("receipt", fixture.required_evidence)
                self.assertTrue(fixture.effect)

    def test_all_four_cases_recompute_as_expected(self):
        report = daf.run_all()
        self.assertEqual(report["cases"], 4)
        self.assertEqual(report["passed"], 3)
        self.assertEqual(report["held"], 1,
                         "the boolean false positive must be correctly HELD")
        self.assertEqual(report["failed"], 0)
        self.assertEqual(report["uncertain"], 1)
        outcomes = {row["experiment"]["name"]: row["result"]["outcome"]
                    for row in report["results"]}
        self.assertEqual(outcomes["boolean_false_positive"], "uncertain")
        for name in ("ocr_band_false_negative", "primary_kept_under_conflict",
                     "submitted_unconfirmed"):
            with self.subTest(name=name):
                self.assertEqual(outcomes[name], "pass")
        for row in report["results"]:
            with self.subTest(name=row["experiment"]["name"]):
                self.assertTrue(row["result"]["matched"])

    def test_six_contract_sections_are_present_everywhere(self):
        for row in daf.run_all()["results"]:
            with self.subTest(name=row["experiment"]["name"]):
                for section in daf.SECTIONS:
                    self.assertIn(section, row)
                self.assertTrue(row["safety"]["no_adapter"])
                self.assertTrue(row["safety"]["no_network"])
                self.assertTrue(row["safety"]["no_write"])
                self.assertTrue(row["safety"]["no_login"])


class RequiredEvidenceTests(unittest.TestCase):
    """A missing artifact is UNCERTAIN, never a pass and never a silent fail."""

    def test_missing_transcript_holds_the_round(self):
        fixture = daf.FIXTURES[0]
        stripped = fixture.with_transcript(None)
        row = daf.evaluate(stripped)
        self.assertEqual(row["result"]["outcome"], "uncertain")
        self.assertFalse(row["result"]["matched"])
        self.assertIn("MISSING_REQUIRED_EVIDENCE", row["result"]["reason"])
        self.assertIn("uia_transcript", row["result"]["reason"])

    def test_prose_success_cannot_pass_without_evidence(self):
        fixture = daf.FIXTURES[0]
        receipt = copy.deepcopy(fixture.receipt)
        receipt["task_delivered"] = True
        receipt["status"] = "message-visible"
        receipt["note"] = "the adapter says delivered"
        stripped = fixture.with_receipt(receipt).with_transcript(None)
        row = daf.evaluate(stripped)
        self.assertEqual(row["result"]["outcome"], "uncertain",
                         "task_delivered=true plus prose is not evidence")
        self.assertFalse(row["result"]["matched"])

    def test_missing_check_fields_are_named(self):
        fixture = daf.FIXTURES[1]  # the 2026-11-05 qoder receipt
        row = daf.evaluate(fixture)
        self.assertEqual(row["result"]["outcome"], "uncertain")
        self.assertIn("check_fields", row["result"]["reason"])
        self.assertIsNone(row["receipt"]["postflight"]["verified"])

    def test_event_id_absent_from_transcript_is_not_a_pass(self):
        fixture = daf.FIXTURES[3]
        row = daf.evaluate(fixture)
        self.assertIsNone(row["receipt"]["postflight"]["transcript_verdict"]["verified"])
        self.assertEqual(row["receipt"]["postflight"]["transcript_verdict"]["reason"],
                         "NO_COMPOSER_BOUNDARY")


class RecomputationIntegrityTests(unittest.TestCase):
    def test_primary_verdict_is_never_relaxed_by_the_second_source(self):
        rows = {r["experiment"]["name"]: r for r in daf.run_all()["results"]}
        conflict = rows["primary_kept_under_conflict"]["receipt"]["postflight"]
        self.assertIs(conflict["verified"], False)
        self.assertIs(conflict["transcript_verdict"]["verified"], True)
        self.assertIs(conflict["sources_in_conflict"], True)
        self.assertTrue(conflict["advisory_only"])

    def test_boolean_false_positive_case_has_no_checks(self):
        rows = {r["experiment"]["name"]: r for r in daf.run_all()["results"]}
        post = rows["boolean_false_positive"]["receipt"]["postflight"]
        self.assertIs(post["declared_task_delivered"], True)
        self.assertEqual(post["checks"], [])
        self.assertIsNone(post["verified"])

    def test_fixture_hash_is_stable_and_detects_drift(self):
        fixture = daf.FIXTURES[0]
        first = daf.evaluate(fixture)["receipt"]["fixture_sha256"]
        second = daf.evaluate(fixture)["receipt"]["fixture_sha256"]
        self.assertEqual(first, second)
        drifted = fixture.with_receipt({**fixture.receipt, "task_delivered": True})
        self.assertNotEqual(daf.evaluate(drifted)["receipt"]["fixture_sha256"], first)

    def test_oracle_mismatch_is_a_failure_not_uncertain(self):
        fixture = daf.FIXTURES[0]
        wrong = fixture.with_oracle({**fixture.oracle, "primary_verified": True})
        row = daf.evaluate(wrong)
        self.assertEqual(row["result"]["outcome"], "fail")
        self.assertIn("ORACLE_MISMATCH", row["result"]["reason"])

    def test_run_all_reports_hard_boundaries(self):
        report = daf.run_all()
        for key in ("models_loaded", "processes_started", "deployment_performed",
                    "system_tasks_registered", "external_login_performed"):
            with self.subTest(key=key):
                self.assertIn(report[key], (0, False))
        self.assertIs(report["ui_input_performed"], False)

    def test_cli_is_read_only_json(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = daf.main(["--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["schema"], daf.SCHEMA)
        self.assertEqual(payload["cases"], 4)


class BoundaryTests(unittest.TestCase):
    def test_no_network_process_model_or_login_imports(self):
        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        banned = {"socket", "urllib", "requests", "httpx", "subprocess",
                  "threading", "asyncio", "insight_proposer",
                  "local_model_session", "webbrowser"}
        seen: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                seen.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                seen.update(a.name for a in node.names)
                if node.module:
                    seen.add(node.module.split(".")[0])
        self.assertFalse(seen & banned, f"forbidden imports: {seen & banned}")

    def test_no_lobehub_runtime_or_credentials(self):
        text = SRC.read_text(encoding="utf-8").lower()
        for token in ("lh login", "lobehub.com", "market-cli", "oauth",
                      "api_key", "access_token"):
            with self.subTest(token=token):
                self.assertNotIn(token, text)
        self.assertIn("independent", text.lower(),
                      "the module must state it reimplements the idea independently")

    def test_module_reuses_existing_verdict_contract(self):
        text = SRC.read_text(encoding="utf-8")
        for token in ("action_verification", "delivery_preflight",
                      "uia_transcript_verdict", "SEND_REPORT_CHECKS"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_capability_card_and_boundary_doc_exist(self):
        card = ROOT / "skills" / "aep-delivery-fixture" / "SKILL.md"
        self.assertTrue(card.is_file())
        doc = (ROOT / "docs" / "DESKTOP_DELIVERY_PREFLIGHT.zh-CN.md").read_text(
            encoding="utf-8")
        self.assertIn("离线验收夹具", doc)


if __name__ == "__main__":
    unittest.main()
