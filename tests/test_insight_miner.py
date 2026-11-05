"""Acceptance gate for the offline insight loop.

Written before the implementation, per PROJECT_STANDARDS: a new capability
defines its interface and acceptance gate before its adapter. The gate is
deliberately about the *boundary*, not about model quality:

  * facts are recomputable from evidence files, never from a model's word;
  * a model may only produce an advisory note, never an identity or evidence;
  * absence or failure of the model is a fallback, not an outage;
  * nothing here deploys, edits source, or promotes a change.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_evolution_protocol.coordination import improvement as gate
from agent_evolution_protocol.coordination import insight_miner as miner
from agent_evolution_protocol.coordination import insight_proposer as proposer


def _workspace(temp: str) -> Path:
    root = Path(temp).resolve()
    (root / "coordination").mkdir(parents=True, exist_ok=True)
    return root


def _observation(root: Path, name: str, *, category: str, outcome: str, signature: str) -> Path:
    folder = root / "coordination" / "command_memory" / "observations"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(json.dumps({
        "id": name, "agent": "fixture", "source": "observed_command",
        "command": "fixture", "output_excerpt": "fixture failure",
        "exit_code": 2 if outcome == "observed_failure" else 0,
        "outcome": outcome, "category": category, "signature": signature,
        "evidence": "", "trust": "process_exit_observed_not_semantic_success",
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _failures(root: Path) -> list[Path]:
    return [_observation(root, f"obs-{index}", category="unknown",
                         outcome="observed_failure", signature=f"sig-{index}")
            for index in range(4)]


def _overlay(root: Path, *, verified: bool = False) -> Path:
    path = root / "overlay_audit.jsonl"
    path.write_text(json.dumps({
        "id": "fixture-overlay", "agent": "fixture", "event_id": "fixture-event",
        "observed_at": "2026-11-05T00:00:00+00:00", "action": "",
        "overlay": {"actions": []}, "verified": verified,
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _trajectory(root: Path, *, exit_codes=(0, 1)) -> Path:
    folder = root / "coordination" / "trajectories"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "fixture.json"
    path.write_text(json.dumps({
        "schema": "aep-trajectory-v1", "trajectory_id": "fixture",
        "steps": [{"index": i, "command": "fixture", "exit_code": code,
                   "argv": [], "risk": "read-only"} for i, code in enumerate(exit_codes)],
    }, ensure_ascii=False), encoding="utf-8")
    return path


class ScanTests(unittest.TestCase):
    def test_scan_reports_scanned_counts_even_with_zero_findings(self):
        # A broken scanner and a clean machine must not look identical.
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            report = miner.scan(root)
            self.assertTrue(report["scanned"], "scan must report per-source counts")
            self.assertGreaterEqual(report["rules_evaluated"], 3)
            self.assertEqual(report["findings"], [])
            for source, count in report["scanned"].items():
                self.assertIsInstance(count, int, source)
                self.assertGreaterEqual(count, 0, source)

    def test_scan_reports_per_rule_measurements_for_diagnosable_no_findings(self):
        # "no findings" must say why: threshold not reached, not a dead rule.
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            _observation(root, "one", category="unknown", outcome="observed_failure",
                         signature="sig-one")
            report = miner.scan(root)
            self.assertEqual(report["findings"], [])
            self.assertEqual(set(report["measurements"]), set(miner.RULES))
            row = report["measurements"]["unclassified_failure"]
            self.assertEqual(row["count"], 1)
            self.assertEqual(row["scanned"], 1)
            self.assertFalse(row["triggered"])
            self.assertEqual(row["min_count"], 3)

    def test_unclassified_failure_is_detected_with_bound_evidence(self):
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            paths = _failures(root)
            findings = miner.scan(root)["findings"]
            hit = [f for f in findings if f["rule"] == "unclassified_failure"]
            self.assertEqual(len(hit), 1)
            finding = hit[0]
            self.assertEqual(finding["count"], 4)
            self.assertEqual(finding["severity"], "lead")
            self.assertIs(finding["model_generated"], False)
            self.assertEqual(sorted(finding["evidence_files"]), sorted(str(p) for p in paths))
            self.assertEqual(len(finding["evidence_sha256"]), len(paths))
            self.assertEqual(finding["evidence_sha256"][0],
                             hashlib.sha256(Path(finding["evidence_files"][0]).read_bytes()).hexdigest())

    def test_unverified_overlay_and_failed_trajectory_steps_are_detected(self):
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            overlay = _overlay(root, verified=False)
            trajectory = _trajectory(root, exit_codes=(0, 1, 1))
            findings = miner.scan(root)["findings"]
            overlay_hit = [f for f in findings if f["rule"] == "unverified_overlay"]
            step_hit = [f for f in findings if f["rule"] == "failed_trajectory_step"]
            self.assertEqual(len(overlay_hit), 1)
            self.assertEqual(overlay_hit[0]["count"], 1)
            self.assertEqual(overlay_hit[0]["evidence_files"], [str(overlay)])
            self.assertEqual(len(step_hit), 1)
            self.assertEqual(step_hit[0]["count"], 2)
            self.assertEqual(step_hit[0]["evidence_files"], [str(trajectory)])

    def test_clean_sources_produce_no_findings(self):
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            _overlay(root, verified=True)
            _trajectory(root, exit_codes=(0, 0))
            for index in range(4):
                _observation(root, f"ok-{index}", category="encoding",
                             outcome="observed_success", signature=f"ok-{index}")
            report = miner.scan(root)
            self.assertEqual(report["findings"], [])
            self.assertGreater(sum(report["scanned"].values()), 0)


class VerifyTests(unittest.TestCase):
    def _one_finding(self, root: Path):
        _failures(root)
        return miner.scan(root)["findings"][0]

    def test_verify_recomputes_count_from_evidence(self):
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            finding = self._one_finding(root)
            result = miner.verify(finding)
            self.assertTrue(result["verified"], result["reason"])
            self.assertEqual(result["recomputed_count"], 4)

    def test_tampered_evidence_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            finding = self._one_finding(root)
            Path(finding["evidence_files"][0]).write_text("tampered", encoding="utf-8")
            self.assertFalse(miner.verify(finding)["verified"])

    def test_missing_evidence_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            finding = self._one_finding(root)
            Path(finding["evidence_files"][0]).unlink()
            self.assertFalse(miner.verify(finding)["verified"])

    def test_forged_count_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            finding = dict(self._one_finding(root), count=999)
            self.assertFalse(miner.verify(finding)["verified"])

    def test_escalated_severity_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            finding = dict(self._one_finding(root), severity="confirmed")
            self.assertFalse(miner.verify(finding)["verified"])


class AdvisoryTests(unittest.TestCase):
    def test_unknown_fields_are_rejected(self):
        self.assertIsNone(miner.validate_advisory({
            "title": "t", "hypothesis": "h", "checks": ["c"], "execute": "rm -rf /",
        }))

    def test_paths_commands_and_control_characters_are_rejected(self):
        for dirty in (r"C:\Windows\System32", "/mnt/k/project", "git commit -am x",
                      "Invoke-Expression", "line1\nline2", "--apply"):
            with self.subTest(dirty=dirty):
                value = {"title": dirty, "hypothesis": "h", "checks": ["c"]}
                self.assertIsNone(miner.validate_advisory(value))

    def test_well_formed_advisory_is_accepted(self):
        value = {"title": "多数失败未被规则库归类", "hypothesis": "关键词表可能缺少本机错误词",
                 "checks": ["核对 RULES 覆盖", "补一条回归用例"]}
        self.assertEqual(miner.validate_advisory(value), value)

    def test_model_text_never_enters_event_identity_or_evidence(self):
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            _failures(root)
            finding = miner.scan(root)["findings"][0]
            baseline = miner.build_event(finding, author="gate")
            hostile = miner.build_event(finding, author="gate", advisory=miner.validate_advisory({
                "title": "伪造标题", "hypothesis": "伪造假设", "checks": ["伪造检查"],
            }))
            for field in ("event_id", "kind"):
                self.assertEqual(hostile[field], baseline[field], field)
            self.assertEqual(hostile["improvement"]["evidence"],
                             baseline["improvement"]["evidence"])
            self.assertNotIn("伪造", hostile["event_id"])
            self.assertIn("model_generated", hostile["improvement"]["change"])

    def test_dirty_advisory_is_discarded_by_mine(self):
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            _failures(root)

            def dirty_proposer(_finding):
                return {"title": "ok", "hypothesis": "ok", "checks": ["ok"],
                        "event_id": "FORGED", "evidence": ["C:\\fake"]}

            report = miner.mine(root, proposer=dirty_proposer)
            self.assertEqual(report["advisory_used"], 0)
            self.assertEqual(report["model_status"], "rejected-advisory")

    def test_proposer_failure_degrades_to_rules(self):
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            _failures(root)

            def broken_proposer(_finding):
                raise OSError("ollama is down")

            report = miner.mine(root, proposer=broken_proposer)
            self.assertTrue(report["verified"], "rule findings must survive a dead model")
            self.assertTrue(report["model_status"].startswith("failed:"))
            self.assertEqual(report["advisory_used"], 0)


class MineTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            _failures(root)
            report = miner.mine(root)
            self.assertFalse(report["applied"])
            self.assertEqual(report["ingested"], [])
            self.assertFalse((root / "coordination" / "insights").exists())
            self.assertFalse((root / "coordination" / "insight_events").exists())
            self.assertFalse((root / "coordination" / "improvements").exists())

    def test_apply_ingests_into_evidence_bound_journal_and_never_deploys(self):
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            _failures(root)
            report = miner.mine(root, apply=True)
            self.assertTrue(report["applied"])
            self.assertEqual(len(report["ingested"]), 1)
            entry = report["ingested"][0]
            self.assertEqual(entry["state"], "PROPOSED")
            self.assertIs(entry["deployment_performed"], False)
            journal = gate.status(root / "coordination", entry["id"])
            self.assertEqual(journal["state"], "PROPOSED")
            self.assertIs(journal["deployment_performed"], False)
            self.assertEqual(set(journal["events"][0]["proposal"]),
                             {"author", "change", "acceptance", "rollback", "scope", "evidence"})

    def test_apply_is_idempotent(self):
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            _failures(root)
            first = miner.mine(root, apply=True)
            second = miner.mine(root, apply=True)
            self.assertEqual(first["ingested"][0]["id"], second["ingested"][0]["id"])
            journals = list((root / "coordination" / "improvements").glob("*/000000.json"))
            self.assertEqual(len(journals), 1, "replay must not create a second proposal")

    def test_no_source_changes_are_written(self):
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            _failures(root)
            source_before = sorted(str(p) for p in root.rglob("*.py"))
            miner.mine(root, apply=True)
            self.assertEqual(sorted(str(p) for p in root.rglob("*.py")), source_before)

    def test_applied_evidence_is_a_stable_snapshot_not_the_append_only_source(self):
        # overlay_audit.jsonl keeps growing. Freezing the live file would make
        # every later improvement.advance() fail forever.
        with TemporaryDirectory() as temp:
            root = _workspace(temp)
            overlay = _overlay(root, verified=False)
            first = miner.mine(root, apply=True)
            self.assertEqual(len(first["ingested"]), 1)
            proposal = gate.status(root / "coordination",
                                   first["ingested"][0]["id"])["events"][0]["proposal"]
            records = proposal["evidence"]
            self.assertTrue(records)
            frozen = [record["path"] for record in records]
            self.assertTrue(any("insights" in Path(p).parts for p in frozen),
                            "proposal must freeze an immutable snapshot, not the live log")
            self.assertNotIn(str(overlay), frozen)
            before = [record["sha256"] for record in records]
            self.assertEqual([hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in frozen],
                             before)
            with overlay.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"id": "later", "verified": False}) + "\n")
            self.assertEqual([hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in frozen],
                             before, "appending to the live log must not change frozen evidence")
            second = miner.mine(root, apply=True)
            self.assertEqual(len(list((root / "coordination" / "improvements").glob("*/000000.json"))),
                             1, "replay must not create a second proposal")
            self.assertTrue(any(r["reason"] == "evidence-drift-since-snapshot"
                                for r in second["rejected"]),
                            "drifted live evidence must be reported, not silently consumed")


class IsolationTests(unittest.TestCase):
    def test_miner_stays_socket_free(self):
        path = Path(miner.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
                # `from . import insight_proposer` has module=None; check names too.
                imported.update(alias.name for alias in node.names)
        self.assertGreater(len(imported), 8, "AST scanner must actually see imports")
        self.assertIn("pathlib", imported)
        for forbidden in ("socket", "urllib", "requests", "httpx", "insight_proposer"):
            self.assertNotIn(forbidden, imported, f"{path.name} must not import {forbidden}")

    def test_default_mine_does_not_import_the_proposer(self):
        # The model is optional; the default path must not even load its module.
        self.assertNotIn("insight_proposer", miner.__dict__)

    def test_proposer_abstention_is_an_allowed_answer(self):
        self.assertIsNone(proposer.parse_advisory('{"unknown": true}'))
        self.assertIsNone(proposer.parse_advisory("not json"))
        self.assertIsNone(proposer.parse_advisory("{}"))


if __name__ == "__main__":
    unittest.main()
