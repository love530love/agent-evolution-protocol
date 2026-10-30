import json
from pathlib import Path
import tempfile
import unittest

from agent_evolution_protocol.coordination import claim_conflicts as cc


def write_claim(root, task, agent, paths, at="2026-10-30T10:00:00+00:00"):
    folder = Path(root) / "claims" / task
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"task": task, "agent": agent, "at": at, "paths": paths}
    (folder / "owner.json").write_text(json.dumps(payload), encoding="utf-8")


class NormalizeTests(unittest.TestCase):
    def test_normalizes_separators_and_dot_segments(self):
        self.assertEqual(cc.normalize("./src\\pkg//mod.py"), "src/pkg/mod.py")

    def test_rejects_escapes_and_absolute_paths(self):
        for bad in ("", "   ", "/etc/passwd", "C:\\Windows", "src/../../etc", ".."):
            with self.assertRaises(ValueError):
                cc.normalize(bad)

    def test_rejects_non_string(self):
        with self.assertRaises(ValueError):
            cc.normalize(None)

    def test_overlap_is_directory_aware(self):
        self.assertTrue(cc.overlaps("src/pkg", "src/pkg/mod.py"))
        self.assertTrue(cc.overlaps("SRC/Pkg", "src/pkg"))
        self.assertFalse(cc.overlaps("src/pkg", "src/pkgx"))
        self.assertFalse(cc.overlaps("src/a", "src/b"))


class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_when_scopes_are_disjoint(self):
        write_claim(self.root, "T-A", "alpha", ["src/a"])
        write_claim(self.root, "T-B", "beta", ["src/b"])
        report = cc.analyze(self.root, now="2026-10-30T11:00:00+00:00")
        self.assertEqual(report["status"], "CLEAN")
        self.assertTrue(report["write_allowed"])
        self.assertEqual(report["conflicts"], [])

    def test_detects_overlap_between_different_tasks(self):
        write_claim(self.root, "T-A", "alpha", ["src/pkg"], at="2026-10-30T09:00:00+00:00")
        write_claim(self.root, "T-B", "beta", ["src/pkg/mod.py"], at="2026-10-30T10:00:00+00:00")
        report = cc.analyze(self.root, now="2026-10-30T11:00:00+00:00")
        self.assertEqual(report["status"], "CONFLICT")
        self.assertFalse(report["write_allowed"])
        overlap = [row for row in report["conflicts"] if row["kind"] == "path_overlap"]
        self.assertEqual(len(overlap), 1)
        self.assertEqual(overlap[0]["severity"], "blocking")
        self.assertEqual(overlap[0]["resolution"]["winner"], "T-A")
        self.assertEqual(overlap[0]["resolution"]["loser"], "T-B")

    def test_earlier_claim_wins_regardless_of_task_order(self):
        write_claim(self.root, "Z-LATE", "beta", ["src/pkg"], at="2026-10-30T12:00:00+00:00")
        write_claim(self.root, "A-EARLY", "alpha", ["src/pkg"], at="2026-10-30T08:00:00+00:00")
        report = cc.analyze(self.root, now="2026-10-30T13:00:00+00:00")
        overlap = [row for row in report["conflicts"] if row["kind"] == "path_overlap"][0]
        self.assertEqual(overlap["resolution"]["winner"], "A-EARLY")

    def test_invalid_scope_blocks_and_is_reported(self):
        write_claim(self.root, "T-A", "alpha", ["../outside"])
        report = cc.analyze(self.root, now="2026-10-30T11:00:00+00:00")
        kinds = [row["kind"] for row in report["conflicts"]]
        self.assertIn("invalid_scope", kinds)
        self.assertEqual(report["status"], "CONFLICT")

    def test_task_only_claim_never_conflicts_on_paths(self):
        write_claim(self.root, "T-A", "alpha", [])
        write_claim(self.root, "T-B", "beta", ["src/pkg"])
        report = cc.analyze(self.root, now="2026-10-30T11:00:00+00:00")
        self.assertEqual(report["task_only_claims"], 1)
        self.assertFalse([row for row in report["conflicts"] if row["kind"] == "path_overlap"])

    def test_stale_claim_is_a_warning_not_a_block(self):
        write_claim(self.root, "T-A", "alpha", ["src/a"], at="2026-10-28T00:00:00+00:00")
        report = cc.analyze(self.root, now="2026-10-30T11:00:00+00:00", stale_after_hours=12)
        stale = [row for row in report["conflicts"] if row["kind"] == "stale_claim"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["severity"], "warning")
        self.assertEqual(report["status"], "CLEAN")

    def test_unreadable_claim_is_blocking(self):
        folder = self.root / "claims" / "T-BAD"
        folder.mkdir(parents=True)
        (folder / "owner.json").write_text("{not json", encoding="utf-8")
        report = cc.analyze(self.root, now="2026-10-30T11:00:00+00:00")
        self.assertEqual(report["status"], "CONFLICT")
        self.assertIn("unreadable_claim", [row["kind"] for row in report["conflicts"]])

    def test_analysis_does_not_mutate_claims(self):
        write_claim(self.root, "T-A", "alpha", ["src/pkg"])
        write_claim(self.root, "T-B", "beta", ["src/pkg/mod.py"])
        before = {path: path.read_bytes() for path in (self.root / "claims").rglob("owner.json")}
        cc.analyze(self.root, now="2026-10-30T11:00:00+00:00")
        after = {path: path.read_bytes() for path in (self.root / "claims").rglob("owner.json")}
        self.assertEqual(before, after)

    def test_report_is_deterministic(self):
        write_claim(self.root, "T-A", "alpha", ["src/pkg"])
        write_claim(self.root, "T-B", "beta", ["src/pkg/mod.py"])
        first = cc.analyze(self.root, now="2026-10-30T11:00:00+00:00")
        second = cc.analyze(self.root, now="2026-10-30T11:00:00+00:00")
        self.assertEqual(first, second)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_blocks_unclaimed_task_that_would_overlap(self):
        write_claim(self.root, "T-A", "alpha", ["src/pkg"])
        result = cc.gate(self.root, agent="beta", task="T-NEW", paths=["src/pkg/mod.py"],
                         now="2026-10-30T11:00:00+00:00")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["allowed"])
        self.assertFalse(result["claim_created"])

    def test_allows_disjoint_scope(self):
        write_claim(self.root, "T-A", "alpha", ["src/pkg"])
        result = cc.gate(self.root, agent="beta", task="T-NEW", paths=["docs/readme.md"],
                         now="2026-10-30T11:00:00+00:00")
        self.assertEqual(result["status"], "ALLOWED")
        self.assertTrue(result["allowed"])

    def test_unreadable_claim_blocks_instead_of_guessing(self):
        folder = self.root / "claims" / "T-BAD"
        folder.mkdir(parents=True)
        (folder / "owner.json").write_text("[]", encoding="utf-8")
        result = cc.gate(self.root, agent="beta", task="T-NEW", paths=["anything"],
                         now="2026-10-30T11:00:00+00:00")
        self.assertEqual(result["status"], "BLOCKED")

    def test_requires_agent_task_and_scope(self):
        with self.assertRaises(ValueError):
            cc.gate(self.root, agent="", task="T", paths=["a"])
        with self.assertRaises(ValueError):
            cc.gate(self.root, agent="a", task="", paths=["a"])
        with self.assertRaises(ValueError):
            cc.gate(self.root, agent="a", task="T", paths=[])


class RemediationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_idle_owner_is_told_to_release_or_narrow(self):
        write_claim(self.root, "T-OLD", "alpha", ["src"], at="2026-10-30T08:00:00+00:00")
        write_claim(self.root, "T-NEW", "beta", ["src/pkg"], at="2026-10-30T09:00:00+00:00")
        states = self.root / "task_states"
        states.mkdir()
        (states / "T-OLD.json").write_text(json.dumps({"task": "T-OLD", "phase": "needs-review"}),
                                           encoding="utf-8")
        plan = cc.remediation(self.root, now="2026-10-30T12:00:00+00:00")
        old = [row for row in plan["actions"] if row["task"] == "T-OLD"][0]
        self.assertEqual(old["action"], "release_or_narrow_claim")
        self.assertFalse(plan["mutated_claims"])

    def test_active_owner_is_told_to_narrow_coarse_scope(self):
        write_claim(self.root, "T-OLD", "alpha", ["src"], at="2026-10-30T08:00:00+00:00")
        write_claim(self.root, "T-NEW", "beta", ["src/pkg"], at="2026-10-30T09:00:00+00:00")
        states = self.root / "task_states"
        states.mkdir()
        (states / "T-OLD.json").write_text(json.dumps({"task": "T-OLD", "phase": "working"}),
                                           encoding="utf-8")
        plan = cc.remediation(self.root, now="2026-10-30T12:00:00+00:00")
        old = [row for row in plan["actions"] if row["task"] == "T-OLD"][0]
        self.assertEqual(old["action"], "narrow_coarse_scope")
        new = [row for row in plan["actions"] if row["task"] == "T-NEW"][0]
        self.assertEqual(new["action"], "narrow_claim_before_writing")

    def test_equal_scopes_only_penalise_the_later_claim(self):
        write_claim(self.root, "T-OLD", "alpha", ["src/pkg"], at="2026-10-30T08:00:00+00:00")
        write_claim(self.root, "T-NEW", "beta", ["src/pkg"], at="2026-10-30T09:00:00+00:00")
        plan = cc.remediation(self.root, now="2026-10-30T12:00:00+00:00")
        self.assertEqual([row["task"] for row in plan["actions"]], ["T-NEW"])

    def test_clean_tree_has_no_actions(self):
        write_claim(self.root, "T-A", "alpha", ["src/a"])
        plan = cc.remediation(self.root, now="2026-10-30T12:00:00+00:00")
        self.assertEqual(plan["actions"], [])
        self.assertEqual(plan["status"], "CLEAN")


if __name__ == "__main__":
    unittest.main()
