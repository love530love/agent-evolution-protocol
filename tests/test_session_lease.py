"""Cross-route original-session lease regressions.

These tests use fake UIA drivers and a local Python process.  They prove the
fence, not a fictional desktop delivery: a successful UI run is accepted only
because the fake driver emits the challenge response expected by the relay.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import json

import pytest

from agent_evolution_protocol.coordination import session_lease
from agent_evolution_protocol.coordination import ui_fallback as ui
from agent_evolution_protocol.coordination.external_runner import execute


PROFILE = {
    "agent": "fake_agent",
    "process": "FakeAgent",
    "title": "Fake Agent",
    "exe": str((Path.cwd() / "FakeAgent.exe").resolve()),
    "session_marker": "Original task chat",
    "model_marker": "Model X",
    "transport": "uia",
    "reply_timeout_seconds": 5,
    "send_mode": "enter",
    "allow_busy_interject": False,
    "interject_hints": ["Interject"],
}


class Driver:
    def __init__(self, *, crash: bool = False, answers: bool = True):
        self.crash = crash
        self.answers = answers
        self.sent = 0
        self.expected_ack = ""

    def observe(self, profile):
        return {"status": "window-observed", "pid": 1234, "input_found": True,
                "input_ambiguous": False, "activity_state": "ready"}

    def capture(self, profile):
        text = "Original task chat Model X"
        if self.answers and self.expected_ack:
            text += "\n" + self.expected_ack
        return text

    def select_session(self, profile):
        return {"status": "not-needed"}

    def launch(self, profile):
        raise AssertionError("test does not permit launching a desktop app")

    def deliver(self, profile, message, event_id, *, urgent=False, composer_ratios=None):
        self.sent += 1
        if self.crash:
            raise KeyboardInterrupt("simulated executor crash")
        import re
        nonce = re.search(r"校验串 ([0-9A-F]+) 的倒序", message)
        assert nonce
        self.expected_ack = "AEP-ACK:" + nonce.group(1)[::-1]
        return {"status": "message-visible", "task_delivered": True,
                "interruption": False}


def _run_ui(root: Path, receipt: Path, driver: Driver, *, event: str = "EVT-LEASE-ONE",
            workspace_id: str = "flaggems-sglang") -> dict:
    return ui.run(PROFILE, "Inspect this", event_id=event, receipt_path=receipt,
                  lock_path=root / "desktop-input.lock", driver=driver,
                  poll_seconds=.1, session_root=root, workspace_id=workspace_id)


class UiLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_session_marker_not_pid_is_the_canonical_key(self):
        first = session_lease.binding(workspace_id="flaggems", provider="uia",
                                      target_agent="qoder", session_id=" Original  chat ")
        second = session_lease.binding(workspace_id="flaggems", provider="uia",
                                       target_agent="qoder", session_id="Original chat")
        assert first == second
        assert "pid" not in first
        with pytest.raises(ValueError, match="must not be a process PID"):
            session_lease.binding(workspace_id="flaggems", provider="uia",
                                  target_agent="qoder", session_id="1234")
        with pytest.raises(ValueError, match="must not be a process PID"):
            session_lease.binding(workspace_id="flaggems", provider="uia",
                                  target_agent="qoder", session_id="pid: 1234")

    def test_uncertain_external_route_blocks_uia_before_any_input(self):
        source = "import time; time.sleep(10)"
        plan = {
            "argv": [__import__("sys").executable, "-c", source],
            "input_capable": True,
            "provider": "uia",
            "target_agent": "fake_agent",
            "session_id": "Original task chat",
            "workspace_id": "flaggems-sglang",
        }
        external = execute(self.root, event_id="EVT-EXTERNAL-ONE", workspace=self.root,
                           plan=plan, timeout=.05)
        assert external["state"] == "UNKNOWN"
        driver = Driver()
        result = _run_ui(self.root, self.root / "uia.json", driver,
                         event="EVT-UI-TWO")
        assert result["state"] == "SESSION_UNCERTAIN"
        assert driver.sent == 0

    def test_distinct_artifact_roots_share_the_explicit_canonical_fence(self):
        coordination = self.root / "coordination"
        external_root = coordination / "external_runs"
        lease_root = session_lease.canonical_root(coordination)
        plan = {
            "argv": [__import__("sys").executable, "-c", "import time; time.sleep(10)"],
            "input_capable": True,
            # This is deliberately a different transport.  The alias is only
            # accepted because the plan carries mapping evidence.
            "provider": "t3code",
            "session_provider": "uia",
            "session_binding_evidence": "verified same Qoder original-session fingerprint",
            "target_agent": "fake_agent",
            "session_id": "Original task chat",
            "workspace_id": "flaggems-sglang",
        }
        external = execute(external_root, event_id="EVT-EXTERNAL-ROOT", workspace=self.root,
                           plan=plan, timeout=.05, session_lease_root=lease_root)
        self.assertEqual(external["state"], "UNKNOWN")
        driver = Driver()
        result = _run_ui(coordination, coordination / "uia.json", driver,
                         event="EVT-UI-OTHER-ROOT")
        self.assertEqual(result["state"], "SESSION_UNCERTAIN")
        self.assertEqual(driver.sent, 0)

    def test_normal_external_process_exit_without_delivery_proof_remains_fenced(self):
        coordination = self.root / "coordination"
        external_root = coordination / "external_runs"
        plan = {
            "argv": [__import__("sys").executable, "-c", "print('local process exit')"],
            "input_capable": True,
            "provider": "t3code",
            "session_provider": "uia",
            "session_binding_evidence": "verified same Qoder original-session fingerprint",
            "target_agent": "fake_agent",
            "session_id": "Original task chat",
            "workspace_id": "flaggems-sglang",
        }
        result = execute(external_root, event_id="EVT-EXIT-NO-RECEIPT", workspace=self.root,
                         plan=plan, session_lease_root=session_lease.canonical_root(coordination))
        self.assertEqual(result["state"], "PROCESS_EXITED")
        binding = session_lease.binding(workspace_id="flaggems-sglang", provider="uia",
                                        target_agent="fake_agent", session_id="Original task chat")
        state = session_lease.read_state(session_lease.canonical_root(coordination), binding,
                                         legacy_roots=(external_root, coordination))
        self.assertEqual(state["state"], "UNCERTAIN")
        blocked = _run_ui(coordination, coordination / "blocked-after-exit.json", Driver(),
                          event="EVT-UI-AFTER-EXIT")
        self.assertEqual(blocked["state"], "SESSION_UNCERTAIN")

    def test_legacy_external_uncertain_sidecar_is_not_lost_during_upgrade(self):
        binding = session_lease.binding(workspace_id="flaggems-sglang", provider="uia",
                                        target_agent="fake_agent", session_id="Original task chat")
        legacy = {"workspace": binding["workspace_id"], "provider": binding["provider"],
                  "target_agent": binding["target_agent"], "session_id": binding["session_id"]}
        old_path = self.root / "session_state" / (session_lease.digest(legacy) + ".json")
        old_path.parent.mkdir(parents=True)
        old_path.write_text(json.dumps({"state": "UNCERTAIN", "event_id": "EVT-OLD"}),
                            encoding="utf-8")
        lease_root = session_lease.canonical_root(self.root)
        seen = session_lease.read_state(lease_root, binding, legacy_roots=(self.root,))
        self.assertTrue(seen["legacy_binding"])
        blocked = _run_ui(self.root, self.root / "blocked.json", Driver(),
                          event="EVT-NEW")
        self.assertEqual(blocked["state"], "SESSION_UNCERTAIN")
        repaired = session_lease.reconcile(
            lease_root, workspace_id="flaggems-sglang", provider="uia",
            target_agent="fake_agent", session_id="Original task chat",
            evidence="reviewed legacy original-session receipt", legacy_roots=(self.root,))
        self.assertTrue(repaired["reconciled"])
        self.assertEqual(session_lease.read_state(lease_root, binding,
                                                  legacy_roots=(self.root,))["state"], "RECONCILED")

    def test_interrupted_uia_send_leaves_fence_until_explicit_reconcile(self):
        crashing = Driver(crash=True)
        with self.assertRaises(KeyboardInterrupt):
            _run_ui(self.root, self.root / "first.json", crashing)
        blocked = _run_ui(self.root, self.root / "second.json", Driver(),
                          event="EVT-LEASE-TWO")
        self.assertEqual(blocked["state"], "SESSION_UNCERTAIN")
        self.assertEqual(blocked["previous_event_id"], "EVT-LEASE-ONE")
        reconciled = session_lease.reconcile(
            session_lease.canonical_root(self.root), workspace_id="flaggems-sglang", provider="uia",
            target_agent="fake_agent", session_id="Original task chat",
            evidence="captured original chat; the interrupted event is absent",
            legacy_roots=(self.root,),
        )
        self.assertTrue(reconciled["reconciled"])
        accepted = _run_ui(self.root, self.root / "third.json", Driver(),
                           event="EVT-LEASE-THREE")
        self.assertEqual(accepted["state"], "AGENT_REPLY_VERIFIED")

    def test_visible_message_without_ack_settles_send_but_records_no_ack(self):
        driver = Driver(answers=False)
        profile = {**PROFILE, "reply_timeout_seconds": .1}
        result = ui.run(profile, "Inspect this", event_id="EVT-NO-ACK",
                        receipt_path=self.root / "no-ack.json",
                        lock_path=self.root / "desktop-input.lock", driver=driver,
                        poll_seconds=.1, session_root=self.root,
                        workspace_id="flaggems-sglang")
        self.assertEqual(result["state"], "NO_REPLY_WITHIN_TIMEOUT")
        binding = session_lease.binding(workspace_id="flaggems-sglang", provider="uia",
                                        target_agent="fake_agent", session_id="Original task chat")
        state = session_lease.read_state(session_lease.canonical_root(self.root), binding,
                                         legacy_roots=(self.root,))
        self.assertEqual(state["state"], "SETTLED")
        self.assertEqual(state["delivery_state"], "MESSAGE_VISIBLE_NO_ACK")

    def test_corrupt_receipt_creates_cross_route_uncertain_fence(self):
        receipt = self.root / "corrupt.json"
        receipt.write_text("{not-json", encoding="utf-8")
        first = _run_ui(self.root, receipt, Driver(), event="EVT-CORRUPT-ONE")
        self.assertEqual(first["state"], "SEND_UNCERTAIN")
        self.assertEqual(first["task_delivered"], False)
        second = _run_ui(self.root, self.root / "other-event.json", Driver(),
                         event="EVT-CORRUPT-TWO")
        self.assertEqual(second["state"], "SESSION_UNCERTAIN")

    def test_implicit_root_uses_desktop_lock_parent_not_receipt_parent(self):
        coordination = self.root / "coordination"
        binding = session_lease.binding(workspace_id="flaggems-sglang", provider="uia",
                                        target_agent="fake_agent", session_id="Original task chat")
        session_lease.write_state(session_lease.canonical_root(coordination), binding,
                                  event_id="EVT-LOCK-PARENT", state="UNCERTAIN")
        driver = Driver()
        # The receipt intentionally sits elsewhere.  A pre-v1 relay would have
        # fenced against that artifact folder and incorrectly typed again.
        result = ui.run(PROFILE, "Inspect this", event_id="EVT-NO-LEASE-ROOT",
                        receipt_path=self.root / "isolated-receipts" / "receipt.json",
                        lock_path=coordination / "desktop-input.lock", driver=driver,
                        poll_seconds=.1, workspace_id="flaggems-sglang")
        self.assertEqual(result["state"], "SESSION_UNCERTAIN")
        self.assertEqual(driver.sent, 0)


def test_short_or_empty_session_marker_is_rejected():
    with pytest.raises(ValueError, match="at least four"):
        session_lease.binding(workspace_id="x", provider="uia", target_agent="q", session_id="pid")

