import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from agent_evolution_protocol import cli


class InspectCliTest(unittest.TestCase):
    def test_t132_detects_owner_split_and_expired_wake_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            coord = root / "coord"
            claim = coord / "claims" / "T132" / "owner.json"
            claim.parent.mkdir(parents=True)
            claim.write_text(json.dumps({"task": "T132", "agent": "workbuddy"}), encoding="utf-8")
            state = coord / "task_states" / "T132.json"
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps({"task": "T132", "owner": "qoder", "phase": "working"}), encoding="utf-8")
            receipt = coord / "wake_pending" / "w1.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps({"id": "w1", "task": "T132", "claim_deadline": "2020-01-01T00:00:00+00:00"}), encoding="utf-8")
            out = StringIO()
            with redirect_stdout(out):
                cli.inspect_system(SimpleNamespace(coord_root=coord, kernel_root=root / "kernel", stale_wake_hours=24, checkpoint_limit=5, markdown=False))
            report = json.loads(out.getvalue())
            self.assertEqual(1, report["summary"]["claim_state_conflicts"])
            self.assertEqual(1, report["summary"]["expired_wake_receipts"])
            self.assertEqual("T132", report["claim_state_conflicts"][0]["task"])

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
