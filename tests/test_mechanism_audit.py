import json
import tempfile
import unittest
from pathlib import Path

from agent_evolution_protocol.coordination.mechanism_audit import audit


class MechanismAuditTest(unittest.TestCase):
    def test_stalled_two_tasks_trigger_proposal_not_code_change(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "metrics.json"
            path.write_text(json.dumps([
                {"task": "T140", "first_experiment_minutes": 20, "cross_review_coverage": 0.8,
                 "repeat_error_rate": 0.2, "information_gain_per_upload": 1.0, "evidence_paths": ["t140.json"]},
                {"task": "T141", "first_experiment_minutes": 25, "cross_review_coverage": 0.5,
                 "repeat_error_rate": 0.3, "information_gain_per_upload": 0.8,
                 "coordination_overhead_pct": 25, "evidence_paths": ["t141.json"]},
            ]), encoding="utf-8")
            report = audit(path)
            self.assertTrue(report["trigger_mechanism_challenge"])
            self.assertEqual(4, len(report["stalled"]))
            self.assertIn("Do not self-modify", report["suggestion"])
