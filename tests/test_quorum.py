import unittest

from agent_evolution_protocol.coordination import quorum


ACTION = "a" * 64
EVIDENCE_1 = "1" * 64
EVIDENCE_2 = "2" * 64
NOW = "2026-10-30T12:00:00+00:00"
OPENED = "2026-10-30T11:30:00+00:00"


def attestation(agent, decision="approve", action=ACTION, evidence=EVIDENCE_1, at="2026-10-30T11:45:00+00:00"):
    return {"agent": agent, "decision": decision, "action_sha256": action,
            "evidence_sha256": evidence, "at": at}


class RequirementTests(unittest.TestCase):
    def test_irreversible_needs_two(self):
        self.assertEqual(quorum.requires("upload")["threshold"], 2)
        self.assertEqual(quorum.requires("publish")["kind"], "irreversible")

    def test_reversible_needs_one(self):
        self.assertEqual(quorum.requires("read")["threshold"], 1)

    def test_explicit_threshold_must_be_positive_int(self):
        self.assertEqual(quorum.requires("read", threshold=3)["threshold"], 3)
        for bad in (0, -1, "2", True):
            with self.assertRaises(ValueError):
                quorum.requires("read", threshold=bad)

    def test_empty_operation_rejected(self):
        with self.assertRaises(ValueError):
            quorum.requires("  ")


class EvaluateTests(unittest.TestCase):
    def evaluate(self, rows, **kwargs):
        options = {"action_sha256": ACTION, "initiator": "alpha", "threshold": 2,
                   "opened_at": OPENED, "now": NOW}
        options.update(kwargs)
        return quorum.evaluate(rows, **options)

    def test_two_independent_approvals_approve(self):
        result = self.evaluate([attestation("beta", evidence=EVIDENCE_1),
                                attestation("gamma", evidence=EVIDENCE_2)])
        self.assertEqual(result["status"], "APPROVED")
        self.assertEqual(result["approvals"], ["beta", "gamma"])
        self.assertEqual(result["remaining"], 0)
        self.assertTrue(result["decision_fingerprint"])
        self.assertFalse(result["executed"])

    def test_single_approval_is_pending(self):
        result = self.evaluate([attestation("beta")])
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["remaining"], 1)
        self.assertEqual(result["decision_fingerprint"], "")

    def test_initiator_cannot_self_approve(self):
        result = self.evaluate([attestation("alpha"), attestation("beta")])
        self.assertEqual(result["status"], "PENDING")
        self.assertIn("initiator cannot attest to their own action",
                      [row["reason"] for row in result["invalid"]])

    def test_duplicate_agent_is_not_double_counted(self):
        result = self.evaluate([attestation("beta"), attestation("beta", evidence=EVIDENCE_2)])
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["approvals"], ["beta"])
        self.assertIn("duplicate attestation from the same agent",
                      [row["reason"] for row in result["invalid"]])

    def test_rejection_wins_over_approvals(self):
        result = self.evaluate([attestation("beta"), attestation("gamma", decision="reject")])
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["rejections"], ["gamma"])

    def test_replayed_approval_for_other_action_is_invalid(self):
        result = self.evaluate([attestation("beta", action="b" * 64), attestation("gamma")])
        self.assertEqual(result["status"], "PENDING")
        self.assertIn("attestation is bound to a different action",
                      [row["reason"] for row in result["invalid"]])

    def test_stale_attestation_is_invalid(self):
        result = self.evaluate([attestation("beta", at="2026-10-30T09:00:00+00:00"),
                                attestation("gamma")],
                               opened_at="2026-10-30T08:00:00+00:00")
        self.assertEqual(result["status"], "PENDING")
        self.assertIn("attestation older than 60 minutes",
                      [row["reason"] for row in result["invalid"]])

    def test_attestation_before_request_is_invalid(self):
        result = self.evaluate([attestation("beta", at="2026-10-30T11:00:00+00:00"),
                                attestation("gamma")])
        self.assertEqual(result["status"], "PENDING")
        self.assertIn("attestation predates the request",
                      [row["reason"] for row in result["invalid"]])

    def test_abstention_does_not_count(self):
        result = self.evaluate([attestation("beta", decision="abstain"), attestation("gamma")])
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["abstentions"], ["beta"])

    def test_bad_evidence_hash_is_invalid(self):
        result = self.evaluate([attestation("beta", evidence="not-a-hash"), attestation("gamma")])
        self.assertEqual(result["status"], "PENDING")

    def test_non_object_attestation_is_reported(self):
        result = self.evaluate(["nope", attestation("beta"), attestation("gamma")])
        self.assertEqual(result["status"], "APPROVED")
        self.assertEqual(result["invalid"][0]["reason"], "attestation must be an object")

    def test_requires_valid_action_hash(self):
        with self.assertRaises(ValueError):
            self.evaluate([], action_sha256="short")

    def test_requires_valid_initiator(self):
        with self.assertRaises(ValueError):
            self.evaluate([], initiator="bad agent id")

    def test_threshold_one_is_reversible_case(self):
        result = self.evaluate([attestation("beta")], threshold=1)
        self.assertEqual(result["status"], "APPROVED")

    def test_decision_is_reproducible(self):
        rows = [attestation("beta"), attestation("gamma", evidence=EVIDENCE_2)]
        self.assertEqual(self.evaluate(rows)["decision_fingerprint"],
                         self.evaluate(rows)["decision_fingerprint"])


class RequiredAttestationsTests(unittest.TestCase):
    def test_wrapper_marks_no_self_attestation(self):
        row = quorum.required_attestations("delete")
        self.assertEqual(row["threshold"], 2)
        self.assertFalse(row["self_attestation_allowed"])
        self.assertFalse(row["executed"])


if __name__ == "__main__":
    unittest.main()
