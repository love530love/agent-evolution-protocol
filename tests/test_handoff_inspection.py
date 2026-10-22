from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_evolution_protocol import cli
from agent_evolution_protocol.coordination import improvement, improvement_work as work, improvement_dispatch as router, commander


class InspectionTests(unittest.TestCase):
    def test_malformed_leases_fail_closed_across_consumers(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            identity, proof = self.setup_proposal(root)
            lease = work.claim(root, identity, actor="worker")
            head = improvement.status(root, identity)["events"][-1]["sha256"]
            commander.submit_order(root, target="worker", task="improvement:" + identity,
                text="fixture", sender="router", event_id="EVT-IMPROVE-" + head[:24].upper())
            bad_rows = [[], {}, {**lease, "expires": float("nan")}, {**lease, "expires": float("inf")},
                {**lease, "expires": True}, {**lease, "active": "false"}, {**lease, "actor": []},
                {**lease, "token": "invalid"}, {**lease, "head": "invalid"},
                {**lease, "deadline": float("nan")}, {**lease, "expires": lease["deadline"] + 1}]
            for row in bad_rows:
                with self.subTest(row_type=type(row).__name__):
                    path = work._path(root, identity)
                    path.write_text(json.dumps(row), encoding="utf-8")
                    before = path.read_bytes()
                    for operation in (
                        lambda: work.check_claim(root, identity, actor="worker", token=lease["token"]),
                        lambda: work.claim(root, identity, actor="next"),
                        lambda: work.renew(root, identity, actor="worker", token=lease["token"]),
                        lambda: work.finish(root, identity, actor="worker", token=lease["token"], release=True)):
                        with self.assertRaises(ValueError):
                            operation()
                    self.assertFalse(work.pending(root)["work"][0]["available"])
                    self.assertEqual(router.delivery_status(root)["orders"][0]["progress"], "DAMAGED")
                    self.assertEqual(work.inspect_handoff(root, identity)["status"], "BLOCKED")
                    self.assertEqual(before, path.read_bytes())

    def setup_proposal(self, root):
        proof = root / "proof"
        proof.write_text("frozen")
        identity = improvement.propose(root, dict(author="a", change="b", acceptance="c",
            rollback="d", scope="e", evidence=[str(proof)]))["id"]
        return identity, proof

    def test_read_only_no_token_and_expiry_is_not_stop_proof(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            identity, proof = self.setup_proposal(root)
            lease = work.claim(root, identity, actor="worker")
            work.checkpoint(root, identity, actor="worker", token=lease["token"], evidence=[str(proof)], note="partial")
            snapshot = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            result = work.inspect_handoff(root, identity)
            self.assertEqual(result["status"], "WAIT_FOR_OWNER")
            self.assertEqual(result["checkpoint_count"], 1)
            self.assertNotIn(lease["token"], json.dumps(result))
            with patch.object(work, "_now", return_value=lease["expires"]):
                result = work.inspect_handoff(root, identity)
            self.assertEqual(result["lease_state"], "EXPIRED")
            self.assertFalse(result["automatic_reclaim_allowed"])
            self.assertFalse(result["execution_stopped_verified"])
            self.assertEqual(snapshot, {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_multiple_errors_aggregated_and_cli_fails_closed(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            identity, proof = self.setup_proposal(root)
            lease = work.claim(root, identity, actor="worker")
            work.checkpoint(root, identity, actor="worker", token=lease["token"], evidence=[str(proof)], note="partial")
            proof.write_text("changed")
            work._path(root, identity).write_text('{"active": "yes"}', encoding="utf-8")
            result = work.inspect_handoff(root, identity)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual({i["code"] for i in result["issues"]},
                {"PROPOSAL_EVIDENCE_CHANGED", "CHECKPOINT_UNUSABLE", "LEASE_UNREADABLE"})
            with redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    cli.coord_improvement(SimpleNamespace(action="inspect-handoff", coord_root=root, id=identity))
            self.assertEqual(stopped.exception.code, 2)

    def test_missing_proposal_does_not_create_files(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            result = work.inspect_handoff(root, "a" * 24)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(list(root.iterdir()), [])
