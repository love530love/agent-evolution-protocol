import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agent_evolution_protocol import cli
from agent_evolution_protocol.coordination import wake_ingress


class WakeIngressTests(unittest.TestCase):
    def _event(self, root: Path, event_id="20261029T000000000000Z-a1b2c3d4"):
        queue = root / "wake_queue"
        queue.mkdir(parents=True)
        event = {
            "id": event_id, "type": "wake_request", "sender": "codex",
            "to": "qoder", "task": "FLAGGEMS-STATUS", "reason": "query",
            "deliver_text": "query battle status", "dry_run": False,
            "requires_user_auth": False, "allow_new_session": False,
        }
        (queue / f"{event_id}.json").write_text(json.dumps(event), encoding="utf-8")
        return event

    def test_preview_is_side_effect_free(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            event = self._event(root)
            result = wake_ingress.dispatch_wake(root, event["id"])
            self.assertFalse(result["dispatch_attempted"])
            self.assertTrue((root / "wake_queue" / f"{event['id']}.json").exists())
            self.assertFalse(list((root / "commander").glob("**/*.json")))

    def test_apply_only_creates_one_order_and_dry_run_never_dispatches(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            event = self._event(root)
            event["dry_run"] = True
            (root / "wake_queue" / f"{event['id']}.json").write_text(json.dumps(event), encoding="utf-8")
            result = wake_ingress.dispatch_wake(root, event["id"], apply=True)
            self.assertEqual("DRY_RUN", result["state"])
            self.assertFalse(list((root / "commander").glob("**/*.json")))
            result2 = wake_ingress.dispatch_wake(root, event["id"])
            self.assertEqual(result, result2)

    def test_apply_is_scoped_and_duplicate_apply_does_not_send_again(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            event = self._event(root)
            def fake_dispatch(workspace, *, order):
                return cli.commander._record_attempt(workspace, order, state="MESSAGE_VISIBLE",
                                                     receipt={"task_delivered": True})
            with patch.object(cli.commander, "dispatch", fake_dispatch):
                first = wake_ingress.dispatch_wake(root, event["id"], apply=True)
                self.assertTrue(first["task_delivered"])
                second = wake_ingress.dispatch_wake(root, event["id"], apply=True)
            self.assertEqual(first["order_id"], second["order_id"])
            self.assertEqual(1, len(list((root / "commander" / "inbox").glob("*.json"))))
            self.assertEqual("MESSAGE_VISIBLE", second["state"])

    def test_cancel_and_apply_share_root_lifecycle(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            event = self._event(root)
            cancelled = wake_ingress.cancel(root, event["id"], actor="codex", reason="superseded")
            self.assertEqual("CANCELLED_BEFORE_CONSUMPTION", cancelled["state"])
            replay = wake_ingress.dispatch_wake(root, event["id"], apply=True)
            self.assertEqual("CANCELLED_BEFORE_CONSUMPTION", replay["state"])
            self.assertFalse(replay["dispatch_attempted"])


if __name__ == "__main__":
    unittest.main()
