"""Project evidence is advisory, time-bounded and self-checking."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_evolution_protocol.coordination import project_evidence as pe


NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def raw(**changes):
    row = {
        "source": "AEGS",
        "repo_root": "F:/PythonProjects1/AEGS",
        "git_head": "a4763c3",
        "handoff_id": "HO-test",
        "handoff_hash": "abc123",
        "takeover_status": "PASS",
        "observed_at": NOW.isoformat(),
    }
    row.update(changes)
    return row


class ProjectEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_a_current_pass_is_sealed_as_advisory_only(self):
        receipt = pe.seal(raw(), workspace_id="aegs", task_id="T-1", now=NOW)
        self.assertTrue(receipt["evidence_id"].startswith("PE-"))
        self.assertTrue(receipt["receipt_hash"])
        self.assertTrue(receipt["advisory_only"])
        self.assertEqual(receipt["authority"], "advisory")
        self.assertFalse(receipt["automatic_takeover_allowed"])
        verdict = pe.verify(receipt, now=NOW)
        self.assertTrue(verdict["valid"])
        self.assertEqual(verdict["status"], pe.PASS)
        self.assertEqual(verdict["reasons"], [])

    def test_an_expired_pass_is_stale_not_held(self):
        receipt = pe.seal(raw(), workspace_id="aegs", max_age_seconds=60, now=NOW)
        verdict = pe.verify(receipt, now=NOW + timedelta(seconds=61))
        self.assertFalse(verdict["valid"])
        self.assertEqual(verdict["status"], pe.STALE)
        self.assertEqual(verdict["reasons"], ["expired"])

    def test_tampering_is_held(self):
        receipt = pe.seal(raw(), workspace_id="aegs", now=NOW)
        receipt["takeover_status"] = "PASS"
        receipt["git_head"] = "f72abb2"
        verdict = pe.verify(receipt, now=NOW)
        self.assertEqual(verdict["status"], pe.HOLD)
        self.assertIn("receipt_hash_mismatch", verdict["reasons"])

    def test_a_source_hold_is_not_reported_as_pass(self):
        receipt = pe.seal(raw(takeover_status="HOLD"), workspace_id="aegs", now=NOW)
        verdict = pe.verify(receipt, now=NOW)
        self.assertEqual(verdict["status"], pe.HOLD)
        self.assertIn("source_takeover_not_pass", verdict["reasons"])

    def test_naive_observation_time_is_refused(self):
        with self.assertRaises(ValueError):
            pe.seal(raw(observed_at="2026-11-15T12:00:00"),
                    workspace_id="aegs", now=NOW)

    def test_automatic_takeover_can_never_be_granted(self):
        with self.assertRaises(ValueError):
            pe.seal(raw(automatic_takeover_allowed=True),
                    workspace_id="aegs", now=NOW)

    def test_record_and_latest_round_trip(self):
        current = datetime.now(timezone.utc)
        receipt = pe.seal(raw(observed_at=current.isoformat()),
                          workspace_id="aegs", now=current)
        target = pe.record(self.root, receipt)
        self.assertTrue(target.is_file())
        latest = pe.latest(self.root, "aegs")
        self.assertTrue(latest["found"])
        self.assertEqual(latest["status"], pe.PASS)
        self.assertEqual(latest["receipt"]["evidence_id"], receipt["evidence_id"])

    def test_latest_reports_stale_after_its_window(self):
        current = datetime.now(timezone.utc)
        receipt = pe.seal(raw(observed_at=current.isoformat()),
                          workspace_id="aegs", max_age_seconds=60, now=current)
        pe.record(self.root, receipt)
        latest = pe.latest(self.root, "aegs", now=current + timedelta(seconds=61))
        self.assertTrue(latest["found"])
        self.assertEqual(latest["status"], pe.STALE)

    def test_workspace_names_cannot_escape_the_receipt_directory(self):
        with self.assertRaises(ValueError):
            pe.seal(raw(), workspace_id="../aegs", now=NOW)

    def test_windows_root_is_valid_when_tests_run_on_posix(self):
        receipt = pe.seal(raw(repo_root="F:\\PythonProjects1\\AEGS"),
                           workspace_id="aegs", now=NOW)
        self.assertEqual(receipt["repo_root"], "F:/PythonProjects1/AEGS")

    def test_posix_root_is_valid_when_tests_run_on_windows(self):
        receipt = pe.seal(raw(repo_root="/srv/aegs"), workspace_id="aegs", now=NOW)
        self.assertEqual(receipt["repo_root"], "/srv/aegs")


if __name__ == "__main__":
    unittest.main()
