"""Risk-gated, non-executing governance contracts."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_evolution_protocol.coordination.governance import (
    assess_intervention, read_interventions, roles, submit_intervention,
)


class GovernanceTests(unittest.TestCase):
    def _request(self, kind="implementation", **changes):
        row = {"type": kind, "by": "author", "target": "dispatch policy",
               "rationale": "bounded proposal with rollback", "evidence_paths": [],
               "reviewers": ["replicator"]}
        row.update(changes)
        return row

    def test_roles_are_descriptive_and_copied(self):
        first = roles()
        self.assertTrue({"catfish", "ideator", "replicator", "referee", "bias_auditor"} <= set(first))
        first["referee"]["capabilities"].append("execute")
        self.assertNotIn("execute", roles()["referee"]["capabilities"])

    def test_no_self_approval_or_auto_apply(self):
        self.assertEqual("REVIEW_REQUIRED", assess_intervention(self._request())["status"])
        self.assertFalse(assess_intervention(self._request())["automatic_application"])
        with self.assertRaises(ValueError):
            assess_intervention(self._request(reviewers=["author"]))
        with self.assertRaises(ValueError):
            assess_intervention(self._request(reviewers=["replicator", "replicator"]))

    def test_high_risk_is_quarantined_and_evidence_frozen(self):
        with TemporaryDirectory() as temp:
            evidence = Path(temp) / "result.txt"
            evidence.write_text("official return", encoding="utf-8")
            request = self._request("dispatch_change", evidence_paths=[str(evidence)])
            row = submit_intervention(Path(temp), request)
            self.assertEqual("L2", row["tier"])
            self.assertEqual("QUARANTINED", row["status"])
            self.assertEqual(64, len(row["evidence"][0]["sha256"]))
            self.assertEqual(row, read_interventions(Path(temp))[0])
            self.assertFalse((Path(temp) / "dispatch policy").exists())

    def test_charter_change_never_auto_applies(self):
        with TemporaryDirectory() as temp:
            proof = Path(temp) / "evidence.txt"
            proof.write_text("review", encoding="utf-8")
            row = submit_intervention(Path(temp), self._request("charter_change", evidence_paths=[str(proof)]))
            self.assertEqual(("L3", "QUARANTINED"), (row["tier"], row["status"]))
            self.assertIn("formal_charter_review", row["required_review"])

    def test_bad_or_missing_evidence_fails_closed(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                submit_intervention(Path(temp), self._request("metric_change"))
            with self.assertRaises(ValueError):
                submit_intervention(Path(temp), self._request("metric_change", evidence_paths=[str(Path(temp) / "absent")]))
            self.assertEqual([], read_interventions(Path(temp)))

    def test_unrecognized_policy_or_non_json_is_rejected(self):
        with self.assertRaises(ValueError):
            assess_intervention(self._request("silent_override"))
        with self.assertRaises(ValueError):
            assess_intervention(self._request(after={"x": object()}))
        with self.assertRaises(ValueError):
            assess_intervention(self._request(extra="not contracted"))


if __name__ == "__main__":
    unittest.main()
