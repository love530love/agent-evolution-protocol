import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from agent_evolution_protocol import cli


class InspectCliTest(unittest.TestCase):
    def test_inspect_reports_hazards_and_recommendations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            coord = root / "coord"
            kernel = root / "kernel"
            cli.coord_bootstrap(SimpleNamespace(coord_root=coord))
            cli.coord_wake(SimpleNamespace(
                coord_root=coord,
                sender="codex",
                to="qoder",
                task="T125",
                reason="test",
                budget="bounded",
                entrypoint="",
                requires_user_auth=False,
                dry_run=True,
                allow_new_session=False,
                claim_timeout_seconds=600,
            ))
            cli.kernel_op_reserve(SimpleNamespace(
                kernel_root=kernel,
                operation_key="op:1",
                request_json='{"task":"T125"}',
                request_file=None,
            ))
            cli.kernel_op_transition(SimpleNamespace(
                kernel_root=kernel,
                operation_key="op:1",
                state="STARTED",
                evidence_json='{"started":true}',
            ))
            cli.kernel_op_transition(SimpleNamespace(
                kernel_root=kernel,
                operation_key="op:1",
                state="UNKNOWN",
                evidence_json='{"timeout":true}',
            ))

            out = StringIO()
            with redirect_stdout(out):
                cli.inspect_system(SimpleNamespace(
                    coord_root=coord,
                    kernel_root=kernel,
                    stale_wake_hours=0,
                    checkpoint_limit=5,
                    markdown=False,
                ))
            report = json.loads(out.getvalue())
            self.assertEqual(1, report["summary"]["wake_queue"])
            self.assertEqual(1, report["summary"]["unknown_operations"])
            self.assertTrue(report["summary"]["missing_sticky_session_ids"])
            self.assertTrue(any("UNKNOWN" in item for item in report["recommendations"]))

            out = StringIO()
            with redirect_stdout(out):
                cli.inspect_system(SimpleNamespace(
                    coord_root=coord,
                    kernel_root=kernel,
                    stale_wake_hours=0,
                    checkpoint_limit=5,
                    markdown=True,
                ))
            self.assertIn("Agent Evolution Runbook", out.getvalue())


if __name__ == "__main__":
    unittest.main()
