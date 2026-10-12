import tempfile
import unittest
from pathlib import Path

from agent_evolution_protocol.coordination.battle_orders import issue, status


class BattleOrderTest(unittest.TestCase):
    def test_assembly_ceasefire_and_independent_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assembled = issue(root, actor="codex", phrase="集结 T136", deputy="workbuddy", recipients=["qoder"])
            self.assertEqual({"workbuddy", "qoder"}, set(assembled["deposited_to"]))
            self.assertFalse(assembled["event"]["execution_requested"])
            with self.assertRaises(ValueError):
                issue(root, actor="qoder", phrase="集结 T136")
            issue(root, actor="qoder", phrase="停火 T136：kunlun code299", evidence_paths=["sub.json"])
            self.assertEqual(1, len(status(root, "T136")["active_holds"]))
            with self.assertRaises(ValueError):
                issue(root, actor="qoder", phrase="停火 T136：kunlun code299", evidence_paths=["sub.json"])
            with self.assertRaises(ValueError):
                issue(root, actor="codex", phrase="开火准备 T136")
            with self.assertRaises(ValueError):
                issue(root, actor="codex", phrase="复火 T136：kunlun code299", evidence_paths=["fix.log"], reviewer="qoder", fix_author="codex")
            issue(root, actor="qoder", phrase="复火 T136：kunlun code299", evidence_paths=["fix.log"], reviewer="qoder", fix_author="codex")
            self.assertEqual([], status(root, "T136")["active_holds"])
            ready = issue(root, actor="codex", phrase="开火准备 T136")
            self.assertFalse(ready["state"]["upload_authorized"])

    def test_handoff_requires_evidence_and_named_deputy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            issue(root, actor="codex", phrase="集结 T140", deputy="workbuddy")
            with self.assertRaises(ValueError):
                issue(root, actor="codex", phrase="换防 T140")
            issue(root, actor="codex", phrase="换防 T140", evidence_paths=["handoff.md"])
            self.assertEqual("workbuddy", status(root, "T140")["lead"])
