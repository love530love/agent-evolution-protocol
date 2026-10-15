import json
import tempfile
import unittest
from pathlib import Path

from agent_evolution_protocol.coordination.joint_attack import (
    open_case, review_case, case_status, submit_adversarial_review, adversarial_status,
)


class JointAttackTest(unittest.TestCase):
    def test_adversarial_chain_rejects_self_review_and_detects_evidence_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = root / "official.json"
            snapshot.write_text(json.dumps({"submission_id": "30164", "chips": {
                "amd": {"status": "passed"},
                "kunlun": {"status": "failed", "raw_error": "compiler error"},
            }}), encoding="utf-8")
            open_case(root, task="T124", submission="30164", lead="codex", deputy="workbuddy",
                      artifact_sha256="a" * 64, result_file=snapshot)
            evidence = root / "compile.log"
            evidence.write_text("verified failure", encoding="utf-8")
            reviews = [
                ("ideator", "codex", {"hypothesis": "split kernel", "predicted_signature": "pass",
                  "cheapest_test": "compile tile", "stop_condition": "same error"}),
                ("falsifier", "qoder", {"counter_hypothesis": "split is insufficient",
                  "discriminating_test": "compile isolated reduction", "predicted_signature": "same error",
                  "stop_condition": "compiler accepts"}),
                ("replicator", "workbuddy", {"protocol": "repeat compile on XPU",
                  "observed_result": "pass", "reproduction_status": "reproduced"}),
                ("bias_auditor", "hermes", {"selection_bias": "only one shape",
                  "missing_cases": "large tile", "audit_conclusion": "hold until all shapes"}),
                ("referee", "copilot", {"verdict": "hold", "rationale": "missing shape",
                  "residual_risk": "compiler regressions"}),
            ]
            for index, (role, agent, data) in enumerate(reviews):
                review_file = root / f"{role}.json"
                review_file.write_text(json.dumps({**data, "evidence_paths": [str(evidence)]}), encoding="utf-8")
                if index == 0:
                    with self.assertRaises(ValueError):
                        submit_adversarial_review(root, task="T124", submission="30164", agent="qoder",
                                                  role="falsifier", review_file=review_file)
                if index == 1:
                    with self.assertRaises(ValueError):
                        submit_adversarial_review(root, task="T124", submission="30164", agent="codex",
                                                  role="falsifier", review_file=review_file)
                submit_adversarial_review(root, task="T124", submission="30164", agent=agent,
                                          role=role, review_file=review_file)
            status = adversarial_status(root, task="T124", submission="30164")
            self.assertEqual("REVIEWED", status["state"])
            self.assertEqual("hold", status["verdict"])
            self.assertFalse(status["upload_ready"])
            evidence.write_text("changed failure", encoding="utf-8")
            drifted = adversarial_status(root, task="T124", submission="30164")
            self.assertEqual("REVIEW_INCOMPLETE", drifted["state"])
            self.assertIn("ideator_invalid", drifted["gaps"][0])

    def test_distinct_reviews_unlock_hypothesis_split_not_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = root / "official.json"
            snapshot.write_text(json.dumps({"submission_id": "30164", "chips": {
                "amd": {"status": "passed"},
                "kunlun": {"status": "failed", "raw_error": "TritonXPULegalize failed"},
            }}), encoding="utf-8")
            opened = open_case(root, task="T124", submission="30164", lead="codex", deputy="workbuddy",
                               artifact_sha256="a" * 64, result_file=snapshot)
            self.assertEqual("DIAGNOSING", opened["state"])
            with self.assertRaises(FileExistsError):
                open_case(root, task="T124", submission="30164", lead="codex", deputy="workbuddy",
                          artifact_sha256="a" * 64, result_file=snapshot)
            review = root / "review.json"
            review.write_text(json.dumps({"hypothesis": "split reduction", "predicted_signature": "compile succeeds",
                                          "cheapest_test": "compile one tile", "stop_condition": "same pass failure",
                                          "evidence_paths": ["compile.log"]}), encoding="utf-8")
            review_case(root, task="T124", submission="30164", agent="qoder", role="cross_platform", review_file=review)
            self.assertEqual("DIAGNOSING", case_status(root, task="T124", submission="30164")["state"])
            review_case(root, task="T124", submission="30164", agent="workbuddy", role="catfish", review_file=review)
            status = case_status(root, task="T124", submission="30164")
            self.assertEqual("HYPOTHESIS_SPLIT", status["state"])
            self.assertFalse(status["upload_ready"])

    def test_failed_chip_requires_raw_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = root / "result.json"
            snapshot.write_text(json.dumps({"submission_id": "1", "chips": {"xpu": {"status": "failed"}}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                open_case(root, task="T130", submission="1", lead="codex", deputy="qoder",
                          artifact_sha256="b" * 64, result_file=snapshot)
