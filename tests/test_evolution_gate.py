import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_evolution_protocol.coordination import evolution_gate


class EvolutionGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / "proof.txt"
        self.evidence.write_text("reviewed", encoding="utf-8")
        self.metrics = self.root / "metrics.json"
        base = {"first_experiment_minutes": 10, "cross_review_coverage": .8,
                "repeat_error_rate": .1, "information_gain_per_upload": 2,
                "evidence_paths": [str(self.evidence)]}
        self.before = {**base, "task": "T1"}
        self.after = {**base, "task": "T2", "first_experiment_minutes": 12,
                      "cross_review_coverage": .7, "repeat_error_rate": .2}
        self.metrics.write_text(json.dumps([self.before, self.after]), encoding="utf-8")

    def test_propose_freezes_source_and_deduplicates(self):
        first = evolution_gate.propose(self.root, self.metrics, change="shorter review",
                                       rollback="restore old routing", author="codex")
        again = evolution_gate.propose(self.root, self.metrics, change="shorter review",
                                       rollback="restore old routing", author="codex")
        self.assertEqual(first, again)
        self.assertEqual(first["state"], "FROZEN_CHALLENGER")
        self.assertFalse(first["automatic_promotion"])
        candidate = self._candidate(first, **{"first_experiment_minutes": 11,
                                                "cross_review_coverage": .75,
                                                "repeat_error_rate": .15})
        self.metrics.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "frozen source changed"):
            evolution_gate.evaluate(self.root, first["id"], candidate)

    def _candidate(self, proposal, **changes):
        row = {**proposal["baseline"], "task": "T3", **changes}
        path = self.root / "candidate.json"
        path.write_text(json.dumps(row), encoding="utf-8")
        return path

    def test_review_gate_requires_no_regression_and_never_promotes(self):
        proposal = evolution_gate.propose(self.root, self.metrics, change="one review change",
                                          rollback="revert", author="codex")
        worse = self._candidate(proposal, repeat_error_rate=.3)
        self.assertEqual(evolution_gate.evaluate(self.root, proposal["id"], worse)["status"], "BASELINE_REQUIRED")
        better = self._candidate(proposal, repeat_error_rate=.15,
                                 first_experiment_minutes=11)
        result = evolution_gate.evaluate(self.root, proposal["id"], better)
        self.assertEqual(result["status"], "EXPERT_REVIEW")
        self.assertFalse(result["automatic_promotion"])

    def test_missing_evidence_and_no_trigger_fail_closed(self):
        self.evidence.unlink()
        with self.assertRaisesRegex(ValueError, "missing baseline evidence"):
            evolution_gate.propose(self.root, self.metrics, change="x", rollback="y", author="z")
        self.evidence.write_text("ok", encoding="utf-8")
        self.metrics.write_text(json.dumps([self.before, {**self.after,
            "first_experiment_minutes": 9, "cross_review_coverage": .9,
            "repeat_error_rate": .05, "information_gain_per_upload": 3}]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no evidenced"):
            evolution_gate.propose(self.root, self.metrics, change="x", rollback="y", author="z")
