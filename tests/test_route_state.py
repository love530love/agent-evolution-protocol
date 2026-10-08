import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_evolution_protocol.route_state import analyze_route_state, continuation_from_events, select_resource_type


class RouteStateTests(unittest.TestCase):
    def test_snapshot_is_read_only_for_missing_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = analyze_route_state(root / "kernel", root / "coord")
            self.assertEqual([], list(root.iterdir()))
            self.assertEqual([], snapshot["active_leases"])

    def test_expiry_release_unknown_and_checkpoint_filter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now = datetime.now(timezone.utc)
            events = [
                {"kind": "LEASE_ACQUIRED", "lease_type": "TASK_WRITE_LEASE", "resource": "old", "expires_at": (now - timedelta(seconds=1)).isoformat()},
                {"kind": "LEASE_ACQUIRED", "lease_type": "TASK_WRITE_LEASE", "resource": "active", "expires_at": (now + timedelta(hours=1)).isoformat()},
                {"kind": "LEASE_ACQUIRED", "lease_type": "TASK_WRITE_LEASE", "resource": "released", "expires_at": (now + timedelta(hours=1)).isoformat()},
                {"kind": "LEASE_RELEASED", "lease_type": "TASK_WRITE_LEASE", "resource": "released"},
                {"kind": "OPERATION", "operation_key": "a", "state": "UNKNOWN", "request": {"task": "T1"}},
                {"kind": "OPERATION", "operation_key": "b", "state": "UNKNOWN", "request": {"task": "T2"}},
                {"kind": "CHECKPOINT_WRITTEN", "task_id": "T1", "checkpoint": {"next_step": "x" * 9000}},
                {"kind": "CHECKPOINT_WRITTEN", "task_id": "T2", "checkpoint": {}},
            ]
            (root / "events.jsonl").write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
            snapshot = analyze_route_state(root, root / "coord", "T1", now=now)
            self.assertEqual(1, len(snapshot["active_leases"]))
            self.assertEqual(1, len(snapshot["expired_leases"]))
            self.assertEqual(1, len(snapshot["unknown_operations"]))
            self.assertEqual(1, len(snapshot["latest_checkpoints"]))
            self.assertLess(len(snapshot["latest_checkpoints"][0]["checkpoint"]["next_step"]), 700)

    def test_waiting_is_not_stale_takeover_and_missing_owner_is_not_free(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            claim = root / "claims" / "T1"
            claim.mkdir(parents=True)
            self.assertEqual("claim-pending-or-unreadable", analyze_route_state(root / "k", root, "T1")["recovery"]["status"])
            old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            (claim / "owner.json").write_text(json.dumps({"agent": "qoder", "at": old}), encoding="utf-8")
            state = root / "task_states" / "T1.json"
            state.parent.mkdir()
            state.write_text(json.dumps({"owner": "qoder", "phase": "waiting", "at": old}), encoding="utf-8")
            recovery = analyze_route_state(root / "k", root, "T1")["recovery"]
            self.assertEqual("waiting", recovery["status"])
            self.assertFalse(recovery["takeover_candidate"])
            state.write_text(json.dumps({"owner": "qoder", "phase": "working", "at": old}), encoding="utf-8")
            recovery = analyze_route_state(root / "k", root, "T1")["recovery"]
            self.assertTrue(recovery["takeover_candidate"])
            self.assertFalse(recovery["automatic_takeover_allowed"])

    def test_corrupt_ledger_warns_and_resume_is_workspace_scoped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "events.jsonl").write_text('{broken', encoding="utf-8")
            self.assertTrue(analyze_route_state(root, root)["warnings"])
            events = [{"kind": "SESSION_BOUND", "workspace": str(root.resolve()), "agent": "codex", "session_id": "same-chat", "resume_supported": True}]
            self.assertEqual("RESUME_EXISTING", continuation_from_events(events, "codex", str(root), {})["action"])
            self.assertEqual("USE_CURRENT_SESSION", continuation_from_events(events, "codex", str(root / "other"), {})["action"])

    def test_upload_requires_identity_and_payment_has_no_executor(self):
        upload = select_resource_type("上传 zip")
        self.assertEqual("UPLOAD_INTENT_LEASE", upload["lease_type"])
        self.assertIn("artifact_sha", upload["required_fields"])
        self.assertEqual("TASK_WRITE_LEASE", select_resource_type("支付")["lease_type"])
