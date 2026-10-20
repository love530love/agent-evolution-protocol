import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent_evolution_protocol.coordination import improvement_acceptance as acceptance, improvement_policy


class AcceptanceTests(unittest.TestCase):
    def test_real_process_and_explicit_simulation_boundaries(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            result = acceptance.run(root)
            self.assertEqual(result["status"], "PASSED", result)
            self.assertTrue(result["actual_test_process"])
            self.assertTrue(result["scripted_roles"])
            self.assertFalse(result["desktop_delivery_verified"])
            self.assertFalse(result["agent_ack_verified"])
            self.assertTrue(all(item["passed"] for item in result["checks"]))
            self.assertEqual(json.loads(Path(result["report"]).read_text())["status"], "PASSED")
            self.assertEqual(improvement_policy.read(root)["mode"], "cooperative")
            second = acceptance.run(root)
            self.assertEqual(second["status"], "PASSED", second)
            self.assertNotEqual(second["run_directory"], result["run_directory"])
