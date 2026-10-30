import unittest

from agent_evolution_protocol.coordination import collab_bench as bench


class CompareTests(unittest.TestCase):
    def test_is_deterministic(self):
        scenario = bench.BUILTIN_SCENARIOS[0]
        self.assertEqual(bench.compare(scenario), bench.compare(scenario))

    def test_overlapping_scopes_duplicate_only_in_solo(self):
        result = bench.compare(bench.BUILTIN_SCENARIOS[0])
        self.assertEqual(result["solo"]["duplicate_side_effects"], 1)
        self.assertEqual(result["coordinated"]["duplicate_side_effects"], 0)
        self.assertEqual(result["coordinated"]["conflict_recall"], 1.0)
        self.assertEqual(result["solo"]["conflict_recall"], 0.0)

    def test_coordination_costs_extra_steps(self):
        result = bench.compare(bench.BUILTIN_SCENARIOS[0])
        self.assertGreater(result["delta"]["steps"], 0)

    def test_dead_agent_loses_task_in_solo_and_recovers_in_coordinated(self):
        result = bench.compare(bench.BUILTIN_SCENARIOS[1])
        self.assertEqual(result["solo"]["completed"], 1)
        self.assertEqual(result["coordinated"]["completed"], 2)
        self.assertEqual(result["coordinated"]["recovery_attempted"], 1)
        self.assertEqual(result["coordinated"]["recovery_succeeded"], 1)

    def test_irreversible_without_second_approver_is_blocked_not_executed(self):
        result = bench.compare(bench.BUILTIN_SCENARIOS[2])
        self.assertEqual(result["solo"]["unsafe_irreversible_executions"], 2)
        self.assertEqual(result["coordinated"]["unsafe_irreversible_executions"], 0)
        self.assertEqual(result["coordinated"]["blocked"], 1)
        self.assertEqual(result["delta"]["blocked"], 1)
        # Blocking is a real cost: one task does not get done.
        self.assertEqual(result["delta"]["completed"], -1)

    def test_evidence_is_labelled_as_simulation(self):
        result = bench.compare(bench.BUILTIN_SCENARIOS[0])
        self.assertEqual(result["evidence"], "SIMULATION_ONLY")
        self.assertTrue(result["limitations"])
        self.assertFalse(result["coordinated"]["completed"] > result["coordinated"]["tasks_total"])

    def test_rejects_bad_scenarios(self):
        with self.assertRaises(ValueError):
            bench.compare({"name": "empty", "tasks": []})
        with self.assertRaises(ValueError):
            bench.compare({"name": "dup", "tasks": [{"id": "a", "steps": 1},
                                                   {"id": "a", "steps": 1}]})
        with self.assertRaises(ValueError):
            bench.compare({"name": "steps", "tasks": [{"id": "a", "steps": 0}]})
        with self.assertRaises(ValueError):
            bench.compare({"name": "scope", "tasks": [{"id": "a", "scope": "../escape"}]})

    def test_rates_are_bounded(self):
        for scenario in bench.BUILTIN_SCENARIOS:
            result = bench.compare(scenario)
            for side in ("solo", "coordinated"):
                self.assertLessEqual(result[side]["completion_rate"], 1.0)
                self.assertLessEqual(result[side]["evidence_coverage"], 1.0)


class SuiteTests(unittest.TestCase):
    def test_suite_aggregates_and_flags_real_world_gap(self):
        suite = bench.run_suite()
        self.assertEqual(suite["evidence"], "SIMULATION_ONLY")
        self.assertEqual(suite["real_world_numbers"], "NOT_VERIFIED")
        self.assertEqual(len(suite["scenarios"]), len(bench.BUILTIN_SCENARIOS))
        for side in ("solo", "coordinated"):
            total = suite["totals"][side]
            self.assertEqual(total["tasks_total"],
                             sum(row[side]["tasks_total"] for row in suite["scenarios"]))
            self.assertLessEqual(total["completed"], total["tasks_total"])

    def test_suite_accepts_custom_scenarios(self):
        suite = bench.run_suite([{"name": "one", "tasks": [{"id": "a", "steps": 2}]}])
        self.assertEqual(len(suite["scenarios"]), 1)
        self.assertEqual(suite["totals"]["solo"]["completed"], 1)


if __name__ == "__main__":
    unittest.main()
