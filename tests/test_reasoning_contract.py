import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_evolution_protocol.coordination import reasoning_contract as contracts


class ReasoningContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = {"task": "T1", "owner": "codex", "objective": "Fix one verified bug",
                        "acceptance": ["Regression test passes"], "mode": "standard",
                        "interaction": "checkpoint", "budget": {"max_rounds": 2, "max_minutes": 10},
                        "stop_conditions": ["Tests pass or budget reached"]}

    def test_freeze_and_readback_without_granting_execution(self):
        result = contracts.create(self.root, self.request)
        self.assertEqual(result, contracts.status(self.root, "T1"))
        self.assertEqual(result["stages"][1], "independent_exploration")
        self.assertFalse(result["execution_started"])
        self.assertFalse(result["authorization_verified"])
        with self.assertRaises(FileExistsError):
            contracts.create(self.root, {**self.request, "objective": "Changed task"})

    def test_changed_frozen_contract_is_rejected(self):
        contracts.create(self.root, self.request)
        path = self.root / "reasoning_contracts" / "T1" / "contract.json"
        row = json.loads(path.read_text(encoding="utf-8"))
        row["contract"]["objective"] = "Another objective"
        path.write_text(json.dumps(row), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "frozen contract changed"):
            contracts.status(self.root, "T1")

    def test_invalid_paths_budgets_and_empty_acceptance_fail_before_write(self):
        cases = ({"task": "../escape"}, {"task": "NUL"}, {"acceptance": []},
                 {"budget": {"max_rounds": True, "max_minutes": 10}},
                 {"budget": {"max_rounds": 2, "max_minutes": float("nan")}})
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                contracts.create(self.root, {**copy.deepcopy(self.request), **changes})
        self.assertFalse((self.root / "reasoning_contracts").exists())
