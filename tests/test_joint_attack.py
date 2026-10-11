import json
import tempfile
import unittest
from pathlib import Path

from agent_evolution_protocol.coordination.joint_attack import open_case, review_case, case_status


class JointAttackTest(unittest.TestCase):
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
