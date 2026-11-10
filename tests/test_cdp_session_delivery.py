"""CDP must obey the same original-session fence as profiled UIA."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agent_evolution_protocol.coordination import cdp_session_delivery as delivery
from agent_evolution_protocol.coordination import session_lease


PROFILE = {
    "agent": "qoder",
    "session_marker": "FlagGems original chat",
    "model_marker": "DeepSeek Flash",
    "session_provider": "native-qoder",
    "session_binding_evidence": "verified anchor plus same original-chat markers",
}


class Socket:
    def close(self):
        pass


class CdpSessionDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name) / "coordination"
        self.receipt = self.root / "receipts" / "EVT-CDP-ONE-qoder.json"

    def tearDown(self):
        self.temp.cleanup()

    def _call(self, **overrides):
        values = {
            "coordination_root": self.root,
            "profile": PROFILE,
            "ws_url": "ws://127.0.0.1:9222/devtools/page/qoder-target-1",
            "target_id": "qoder-target-1",
            "text": "Please inspect AEP-ACK:EVT-CDP-ONE",
            "event_id": "EVT-CDP-ONE",
            "receipt_path": self.receipt,
            "wait_seconds": 0.01,
        }
        values.update(overrides)
        return delivery.deliver(**values)

    def _preflight(self, text="FlagGems original chat\nDeepSeek Flash"):
        return (
            patch.object(delivery.transport_cdp, "connect", return_value=Socket()),
            patch.object(delivery.transport_cdp, "read_transcript", return_value=text),
            patch.object(delivery.transport_cdp, "locate_composer", return_value={"tag": "TEXTAREA"}),
        )

    def test_visible_cdp_message_settles_the_shared_original_session_lease(self):
        connect, transcript, composer = self._preflight()
        with (connect, transcript, composer,
              patch.object(delivery.transport_cdp, "deliver", return_value={
                  "status": "message-visible", "task_delivered": True,
                  "agent_accepted": False,
              }) as send):
            result = self._call()
        self.assertEqual(result["status"], "MESSAGE_VISIBLE")
        self.assertTrue(result["task_delivered"])
        self.assertFalse(result["agent_accepted"])
        send.assert_called_once()
        binding = session_lease.binding(
            workspace_id=str(self.root.resolve()), provider="native-qoder",
            target_agent="qoder", session_id="FlagGems original chat")
        state = session_lease.read_state(session_lease.canonical_root(self.root), binding,
                                         legacy_roots=(self.root,))
        self.assertEqual(state["state"], "SETTLED")
        self.assertEqual(state["delivery_state"], "MESSAGE_VISIBLE")

    def test_active_uia_or_other_route_fence_blocks_cdp_before_connecting(self):
        binding = session_lease.binding(
            workspace_id=str(self.root.resolve()), provider="native-qoder",
            target_agent="qoder", session_id="FlagGems original chat")
        session_lease.write_state(session_lease.canonical_root(self.root), binding,
                                  event_id="EVT-UIA-FIRST", state="UNCERTAIN")
        with patch.object(delivery.transport_cdp, "connect") as connect:
            result = self._call()
        self.assertEqual(result["status"], "SESSION_UNCERTAIN")
        connect.assert_not_called()

    def test_missing_canonical_provider_refuses_before_cdp_connection(self):
        profile = {key: value for key, value in PROFILE.items()
                   if key not in {"session_provider", "session_binding_evidence"}}
        with patch.object(delivery.transport_cdp, "connect") as connect:
            result = self._call(profile=profile)
        self.assertEqual(result["status"], "SESSION_BINDING_REQUIRED")
        connect.assert_not_called()

    def test_mismatched_cdp_endpoint_is_proven_not_sent_before_connection(self):
        with patch.object(delivery.transport_cdp, "connect") as connect:
            result = self._call(ws_url="ws://127.0.0.1:9222/devtools/page/other-tab")
        self.assertEqual(result["status"], "TARGET_ID_MISMATCH")
        self.assertFalse(result["task_delivered"])
        connect.assert_not_called()

    def test_preinput_target_mismatch_preserves_an_existing_receipt(self):
        original = {"event_id": "EVT-OLDER", "request_sha256": "preserve-me"}
        self.receipt.parent.mkdir(parents=True, exist_ok=True)
        self.receipt.write_text(json.dumps(original), encoding="utf-8")
        with patch.object(delivery.transport_cdp, "connect") as connect:
            result = self._call(ws_url="ws://127.0.0.1:9222/devtools/page/other-tab")
        self.assertEqual(result["status"], "TARGET_ID_MISMATCH")
        self.assertEqual(json.loads(self.receipt.read_text(encoding="utf-8")), original)
        self.assertTrue(Path(result["outcome_receipt_path"]).is_file())
        connect.assert_not_called()

    def test_wrong_original_chat_is_proven_not_sent_before_dom_delivery(self):
        connect, transcript, composer = self._preflight("Other chat\nDeepSeek Flash")
        with (connect, transcript, composer,
              patch.object(delivery.transport_cdp, "deliver") as send):
            result = self._call()
        self.assertEqual(result["status"], "SESSION_NOT_VERIFIED")
        self.assertFalse(result["task_delivered"])
        send.assert_not_called()
        binding = session_lease.binding(
            workspace_id=str(self.root.resolve()), provider="native-qoder",
            target_agent="qoder", session_id="FlagGems original chat")
        state = session_lease.read_state(session_lease.canonical_root(self.root), binding,
                                         legacy_roots=(self.root,))
        self.assertEqual(state["state"], "PROVEN_NOT_SENT")

    def test_post_send_uncertainty_blocks_a_second_original_session_write(self):
        connect, transcript, composer = self._preflight()
        with (connect, transcript, composer,
              patch.object(delivery.transport_cdp, "deliver", return_value={
                  "status": "submitted-unconfirmed", "task_delivered": False,
                  "agent_accepted": False,
              })):
            first = self._call()
        self.assertEqual(first["status"], "submitted-unconfirmed")
        second_receipt = self.root / "receipts" / "EVT-CDP-TWO-qoder.json"
        with patch.object(delivery.transport_cdp, "connect") as connect:
            second = self._call(event_id="EVT-CDP-TWO", receipt_path=second_receipt,
                                text="Please inspect AEP-ACK:EVT-CDP-TWO")
        self.assertEqual(second["status"], "SESSION_UNCERTAIN")
        connect.assert_not_called()

    def test_conflicting_receipt_is_preserved_and_outcome_is_written_beside_it(self):
        original = {
            "event_id": "EVT-OTHER-REQUEST",
            "request_sha256": "the-existing-request-must-not-be-overwritten",
            "status": "MESSAGE_VISIBLE",
        }
        self.receipt.parent.mkdir(parents=True, exist_ok=True)
        self.receipt.write_text(json.dumps(original), encoding="utf-8")
        with patch.object(delivery.transport_cdp, "connect") as connect:
            result = self._call()
        self.assertEqual(result["status"], "RECEIPT_COLLISION")
        self.assertEqual(json.loads(self.receipt.read_text(encoding="utf-8")), original)
        outcome = Path(result["outcome_receipt_path"])
        self.assertNotEqual(outcome, self.receipt)
        self.assertTrue(outcome.is_file())
        self.assertEqual(json.loads(outcome.read_text(encoding="utf-8"))["status"],
                         "RECEIPT_COLLISION")
        connect.assert_not_called()

    def test_unreadable_receipt_is_preserved_and_fences_the_session(self):
        self.receipt.parent.mkdir(parents=True, exist_ok=True)
        self.receipt.write_text("not-json-and-not-replaceable", encoding="utf-8")
        with patch.object(delivery.transport_cdp, "connect") as connect:
            result = self._call()
        self.assertEqual(result["status"], "SEND_UNCERTAIN")
        self.assertEqual(self.receipt.read_text(encoding="utf-8"), "not-json-and-not-replaceable")
        outcome = Path(result["outcome_receipt_path"])
        self.assertTrue(outcome.is_file())
        self.assertEqual(json.loads(outcome.read_text(encoding="utf-8"))["status"],
                         "SEND_UNCERTAIN")
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
