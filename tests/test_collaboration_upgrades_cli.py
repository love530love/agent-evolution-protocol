import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from agent_evolution_protocol import cli


class CollaborationUpgradesCliTests(unittest.TestCase):
    def test_new_commands_are_parseable(self):
        parser = cli.build_parser()
        cases = (
            ["coord-governance", "roles"],
            ["coord-reasoning", "status", "--task", "T1"],
            ["coord-desktop", "inventory"],
            ["coord-adversarial", "status", "--task", "T1", "--submission", "S1"],
            ["coord-legion", "incentive-board"],
            ["coord-legion", "adjudicate", "--event-id", "e", "--reviewer", "r",
             "--verdict", "accepted", "--note", "proof"],
        )
        for argv in cases:
            with self.subTest(argv=argv):
                self.assertTrue(callable(parser.parse_args(argv).func))

    def test_governance_cli_only_records_an_intervention(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            proof = root / "proof.txt"
            proof.write_text("observed", encoding="utf-8")
            request = root / "request.json"
            request.write_text(json.dumps({"type": "metric_change", "by": "codex",
                "target": "coordination metric", "rationale": "test a different measure",
                "evidence_paths": [str(proof)], "reviewers": ["qoder"],
                "after": {"threshold": 0.5}}), encoding="utf-8")
            with redirect_stdout(StringIO()) as output:
                cli.coord_governance(SimpleNamespace(coord_root=root, action="intervene",
                                                      request_file=request))
            row = json.loads(output.getvalue())
            self.assertEqual(row["status"], "QUARANTINED")
            self.assertFalse(row["automatic_application"])
            with redirect_stdout(StringIO()) as output:
                cli.coord_governance(SimpleNamespace(coord_root=root, action="history",
                                                      request_file=None))
            self.assertEqual(json.loads(output.getvalue())[0]["id"], row["id"])
