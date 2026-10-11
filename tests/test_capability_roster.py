import tempfile
import unittest
from pathlib import Path

from agent_evolution_protocol.coordination.capability_roster import declare, match


class CapabilityRosterTest(unittest.TestCase):
    def test_explicit_match_never_implies_ack(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            declare(root, agent="qoder", chips=["kunlun", "amd"], compilers=["triton_xpu"],
                    strengths=["compiler", "kernel_split"], evidence=["compile-report.md"], ttl_minutes=10)
            result = match(root, chip="kunlun", compiler="triton_xpu")
            self.assertEqual("qoder", result["active"][0]["agent"])
            self.assertIn("does not wake or assign", result["note"])
            self.assertFalse(match(root, chip="ascend")["active"])

    def test_evidence_and_bounded_ttl_required(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                declare(Path(temp), agent="qoder", chips=["kunlun"], compilers=["xpu"],
                        strengths=["compiler"], evidence=[], ttl_minutes=10)
            with self.assertRaises(ValueError):
                declare(Path(temp), agent="qoder", chips=["kunlun"], compilers=["xpu"],
                        strengths=["compiler"], evidence=["x"], ttl_minutes=1000)
