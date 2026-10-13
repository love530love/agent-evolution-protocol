"""Regression tests for wrong-client sends and self-acknowledgement."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_evolution_protocol.coordination import commander, transcript_bus, transport_cdp


class CommanderDeliverySafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_unbound_debug_port_is_never_selected(self):
        with patch.object(commander.cdp, "probe") as probe:
            transport, info = commander._pick_transport(self.root, "workbuddy")
        self.assertEqual((transport, info), ("uia", {}))
        probe.assert_not_called()

    def test_unrelated_debug_port_is_rejected(self):
        try:
            import psutil
        except ImportError:
            self.skipTest("optional psutil dependency unavailable")

        listener = SimpleNamespace(laddr=SimpleNamespace(port=9222),
                                   status=psutil.CONN_LISTEN, pid=88)
        process = SimpleNamespace(pid=88, parents=lambda: [])
        anchor = SimpleNamespace(pid=99, parents=lambda: [])
        with (patch.object(psutil, "net_connections", return_value=[listener]),
              patch.object(psutil, "Process", side_effect=lambda pid: process if pid == 88 else anchor)):
            self.assertFalse(commander._cdp_port_owned_by_anchor(9222, 99))

    def test_outgoing_token_is_not_agent_ack(self):
        order = commander.submit_order(self.root, target="workbuddy", task="test",
                                       text="Please inspect the local inbox", sender="human")
        order["session_id"] = "123"
        commander._record_attempt(self.root, order, state="MESSAGE_VISIBLE")
        transcript_bus.publish(self.root, agent="workbuddy", text=order["text"],
                               session_id="123", source="test")
        self.assertEqual(commander.check_acks(self.root)["acked"], [])
        self.assertEqual(commander._orders(self.root)[0]["state"], "MESSAGE_VISIBLE")

    def test_role_attributed_same_session_reply_can_ack(self):
        order = commander.submit_order(self.root, target="workbuddy", task="test",
                                       text="Please inspect the local inbox", sender="human")
        order["session_id"] = "123"
        commander._record_attempt(self.root, order, state="MESSAGE_VISIBLE")
        transcript_bus.publish(self.root, agent="workbuddy",
                               text="Accepted AEP-ACK:" + order["event_id"],
                               session_id="999", role="assistant", direction="incoming")
        self.assertEqual(commander.check_acks(self.root)["acked"], [])
        transcript_bus.publish(self.root, agent="workbuddy",
                               text="Confirmed AEP-ACK:" + order["event_id"],
                               session_id="123", role="assistant", direction="incoming")
        self.assertEqual(len(commander.check_acks(self.root)["acked"]), 1)
        self.assertEqual(commander._orders(self.root)[0]["state"], "AGENT_ACK")

    def test_cdp_cannot_claim_delivery_without_submit_control(self):
        class Socket:
            def close(self):
                pass

        with (patch.object(transport_cdp, "connect", return_value=Socket()),
              patch.object(transport_cdp, "locate_composer", return_value={"tag": "TEXTAREA"}),
              patch.object(transport_cdp, "type_message", return_value={"ok": True}),
              patch.object(transport_cdp, "submit", return_value={"submitted": False,
                                                                   "reason": "NO_FORM_BOUND_TO_COMPOSER"}),
              patch.object(transport_cdp, "read_transcript") as read):
            receipt = transport_cdp.deliver("ws://127.0.0.1:1/x", "hello AEP-ACK:EVT-1",
                                            event_id="EVT-1")
        self.assertEqual(receipt["status"], "SUBMIT_UNVERIFIED")
        self.assertFalse(receipt["task_delivered"])
        read.assert_not_called()

    def test_failed_uia_adapter_is_not_counted_as_input_submitted(self):
        order = commander.submit_order(self.root, target="qoder", task="notice",
                                       text="Read this warning", sender="codex")
        client = {"agent": "qoder", "windows": [{"pid": 123}],
                  "unique": True, "anchored": False}
        with (patch.object(commander.registry, "discover", return_value={"clients": [client]}),
              patch.object(commander, "_pick_transport", return_value=("uia", {})),
              patch.object(commander, "_uia_deliver", return_value={
                  "status": "NO_RECEIPT", "returncode": 1, "task_delivered": False})):
            result = commander.dispatch(self.root, order=order)
        self.assertEqual(result["state"], "CEASED")
        self.assertIn("DELIVERY_UNPROVEN", result["attempts"][-1]["note"])

    def test_profiled_original_session_route_precedes_raw_uia(self):
        order = commander.submit_order(self.root, target="qoder", task="notice",
                                       text="Read this warning", sender="codex")
        profile = self.root / "ui_profiles" / "qoder.json"
        profile.parent.mkdir(parents=True)
        profile.write_text("{}", encoding="utf-8")
        client = {"agent": "qoder", "windows": [{"pid": 123}],
                  "unique": True, "anchored": False}
        with (patch.object(commander.registry, "discover", return_value={"clients": [client]}),
              patch.object(commander, "_pick_transport", return_value=("uia", {})),
              patch.object(commander, "_profiled_deliver", return_value={
                  "status": "AGENT_REPLY_VERIFIED", "task_delivered": True,
                  "agent_accepted": True, "evidence": "receipt.json"}) as profiled,
              patch.object(commander, "_uia_deliver") as raw):
            result = commander.dispatch(self.root, order=order)
        self.assertEqual(result["state"], "AGENT_ACK")
        self.assertEqual(result["ack"]["evidence"], "receipt.json")
        profiled.assert_called_once()
        raw.assert_not_called()


if __name__ == "__main__":
    unittest.main()
