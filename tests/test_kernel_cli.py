import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_evolution_protocol import cli


class KernelCliTest(unittest.TestCase):
    def test_lease_operation_session_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            cli.kernel_lease_acquire(SimpleNamespace(
                kernel_root=root,
                lease_type="BROWSER_SESSION_LEASE",
                resource="tab:1",
                executor="codex",
                task_id="",
                artifact_sha="",
                ttl_seconds=60,
            ))
            events = (root / "events.jsonl").read_text(encoding="utf-8").splitlines()
            first = json.loads(events[-1])
            self.assertEqual("LEASE_ACQUIRED", first["kind"])
            self.assertEqual(1, first["lease_epoch"])

            with self.assertRaises(RuntimeError):
                cli.kernel_lease_acquire(SimpleNamespace(
                    kernel_root=root,
                    lease_type="BROWSER_SESSION_LEASE",
                    resource="tab:1",
                    executor="qoder",
                    task_id="",
                    artifact_sha="",
                    ttl_seconds=60,
                ))

            cli.kernel_lease_release(SimpleNamespace(
                kernel_root=root,
                lease_type="BROWSER_SESSION_LEASE",
                resource="tab:1",
                executor="codex",
                lease_epoch=1,
            ))

            cli.kernel_op_reserve(SimpleNamespace(
                kernel_root=root,
                operation_key="upload:T125:r1",
                request_json='{"task":"T125","artifact":"abc"}',
                request_file=None,
            ))
            cli.kernel_op_transition(SimpleNamespace(
                kernel_root=root,
                operation_key="upload:T125:r1",
                state="STARTED",
                evidence_json='{"started_by":"codex"}',
            ))
            cli.kernel_op_transition(SimpleNamespace(
                kernel_root=root,
                operation_key="upload:T125:r1",
                state="COMMITTED",
                evidence_json='{"receipt":"ok"}',
            ))

            cli.kernel_session_bind(SimpleNamespace(
                kernel_root=root,
                agent="workbuddy",
                provider="hermes",
                workspace=str(root),
                session_id="session-1",
                no_resume=False,
            ))
            plan = cli.ClusterKernel(root).continuation_plan("workbuddy", str(root))
            self.assertEqual("RESUME_EXISTING", plan.action)

            checkpoint = {
                "completed": ["read digest"],
                "next_step": "run falsification",
                "artifacts": [],
                "pending_operations": [],
                "unknowns": [],
            }
            cli.kernel_checkpoint(SimpleNamespace(
                kernel_root=root,
                task_id="T125",
                executor="codex",
                payload_json=json.dumps(checkpoint),
                payload_file=None,
            ))
            self.assertTrue(cli.ClusterKernel(root).verify_chain())


if __name__ == "__main__":
    unittest.main()
