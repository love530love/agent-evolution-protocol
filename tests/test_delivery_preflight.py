"""Acceptance gate for the read-only desktop delivery preflight.

The live dispatch path currently trusts a ``task_delivered`` boolean and has
no component that answers, before any input, "can this be sent, by which
route, and what is missing".  This gate keeps the fix honest:

* purely read-only -- no pwsh, no typing, no window focus, no state change;
* reuse ``input_routing.delivery_verdict`` instead of inventing a second
  decision rule;
* a post-send receipt is recomputed with ``action_verification`` but is
  ADVISORY ONLY: commander still owns the live path and must not be switched
  over without a real-machine measurement.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import unittest

from agent_evolution_protocol.coordination import action_verification as av
from agent_evolution_protocol.coordination import delivery_preflight as dp

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "agent_evolution_protocol" / "coordination" / "delivery_preflight.py"
AGENT = "qoder"


def _observe(**overrides) -> dict:
    row = {"agent": AGENT, "mode": "observe", "status": "window-observed",
           "input_found": True, "input_region": {"x": 1, "y": 2, "width": 3, "height": 4},
           "activity_state": "ready", "session_marker_check": "seen",
           "transcript_chars": 2048, "task_delivered": False, "pid": 1234}
    row.update(overrides)
    return row


def _profile(**overrides) -> dict:
    row = {"agent": AGENT, "transport": "uia", "session_marker": "赛事夺冠执行",
           "send_mode": "enter", "ocr_language": "zh-Hans-CN",
           "interject_after_send": True, "reply_timeout_seconds": 120}
    row.update(overrides)
    return row


def _root(*, profile=True, observe=True, health=True, **observe_kw):
    holder = tempfile.TemporaryDirectory()
    root = Path(holder.name) / "coord"
    (root / "ui_profiles").mkdir(parents=True)
    (root / "uia_receipts").mkdir(parents=True)
    profile_kw = {k[len("profile_"):]: v for k, v in observe_kw.items()
                  if k.startswith("profile_")}
    observe_kw = {k: v for k, v in observe_kw.items()
                  if not k.startswith("profile_")}
    if profile:
        (root / "ui_profiles" / f"{AGENT}.json").write_text(
            json.dumps(_profile(**profile_kw)), encoding="utf-8")
    if observe:
        (root / "uia_receipts" / f"last-{AGENT}.json").write_text(
            json.dumps(_observe(**observe_kw)), encoding="utf-8")
    if health:
        (root / "desktop_health.json").write_text(json.dumps(
            {"component": "native-window-enumeration", "status": "READY",
             "circuit": "READY", "consecutive_failures": 0}), encoding="utf-8")
    return root, holder


class PreflightTests(unittest.TestCase):
    def test_complete_observation_is_ready_but_still_unverified(self):
        root, holder = _root()
        try:
            report = dp.preflight(root, AGENT)
            self.assertEqual(report["missing"], [])
            self.assertEqual(report["verdict"]["action"], "send-after-verification")
            self.assertEqual(report["readiness"], "READY_UNVERIFIED")
            self.assertEqual(report["route"], "uia")
            self.assertIs(report["ui_input_performed"], False)
            self.assertIn("uia_receipts", report["evidence"]["sources"]["observe"])
        finally:
            holder.cleanup()

    def test_missing_profile_is_named_and_defers(self):
        root, holder = _root(profile=False)
        try:
            report = dp.preflight(root, AGENT)
            self.assertIn("PROFILE_MISSING", report["missing"])
            self.assertEqual(report["verdict"]["action"], "defer")
            self.assertEqual(report["readiness"], "DEFERRED")
        finally:
            holder.cleanup()

    def test_missing_observe_is_named_and_defers(self):
        root, holder = _root(observe=False)
        try:
            report = dp.preflight(root, AGENT)
            self.assertIn("OBSERVE_MISSING", report["missing"])
            self.assertEqual(report["readiness"], "DEFERRED")
        finally:
            holder.cleanup()

    def test_capture_receipt_cannot_be_used_as_a_preflight(self):
        root, holder = _root(mode="capture", status="transcript-captured")
        try:
            report = dp.preflight(root, AGENT)
            self.assertIn("CAPTURE_ONLY_RECEIPT", report["missing"])
            self.assertEqual(report["readiness"], "DEFERRED")
        finally:
            holder.cleanup()

    def test_each_hard_gate_is_named(self):
        cases = (
            ({"session_marker_check": "transcript-unreadable"}, "TRANSCRIPT_UNREADABLE"),
            ({"session_marker_check": "missing"}, "SESSION_MARKER_NOT_SEEN"),
            ({"input_found": False}, "INPUT_NOT_FOUND"),
            ({"input_region": None}, "INPUT_REGION_MISSING"),
            ({"activity_state": "busy", "profile_require_activity_ready": True},
             "ACTIVITY_BUSY"),
        )
        for kwargs, expected in cases:
            with self.subTest(expected=expected):
                root, holder = _root(**kwargs)
                try:
                    report = dp.preflight(root, AGENT)
                    self.assertIn(expected, report["missing"])
                    self.assertEqual(report["readiness"], "DEFERRED")
                finally:
                    holder.cleanup()

    def test_degraded_window_enumeration_is_a_gate(self):
        root, holder = _root()
        try:
            (root / "desktop_health.json").write_text(json.dumps(
                {"circuit": "DEGRADED", "status": "DEGRADED",
                 "consecutive_failures": 2}), encoding="utf-8")
            self.assertIn("WINDOW_ENUMERATION_DEGRADED",
                          dp.preflight(root, AGENT)["missing"])
        finally:
            holder.cleanup()

    def test_ocr_profile_selects_the_ocr_route(self):
        root, holder = _root(profile_transport="ocr")
        try:
            report = dp.preflight(root, AGENT)
            self.assertEqual(report["route"], "ocr")
            self.assertIn("OCR", report["notes"])
        finally:
            holder.cleanup()

    def test_missing_client_registration_is_a_warning_not_a_gate(self):
        root, holder = _root()
        try:
            report = dp.preflight(root, AGENT)
            self.assertIn("CLIENT_NOT_REGISTERED", report["warnings"])
            self.assertEqual(report["missing"], [])
        finally:
            holder.cleanup()

    def test_preflight_never_writes_anything(self):
        root, holder = _root()
        try:
            before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            dp.preflight(root, AGENT)
            dp.run(root)
            after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            self.assertEqual(before, after)
        finally:
            holder.cleanup()


class PostflightTests(unittest.TestCase):
    def test_a_bare_task_delivered_boolean_is_not_evidence(self):
        result = dp.postflight({"task_delivered": True, "status": "message-visible"})
        self.assertIsNone(result["verified"])
        self.assertTrue(result["declared_task_delivered"])
        self.assertTrue(result["advisory_only"])
        self.assertEqual(result["checks"], [])

    def test_all_four_checks_present_and_consistent_verifies(self):
        report = {field: expected for field, (_, expected)
                  in av.SEND_REPORT_CHECKS.items()}
        report["task_delivered"] = True
        result = dp.postflight(report)
        self.assertIs(result["verified"], True)
        self.assertEqual(len(result["checks"]), 4)

    def test_a_refuted_check_blocks_verification(self):
        report = {field: expected for field, (_, expected)
                  in av.SEND_REPORT_CHECKS.items()}
        report["probe_in_transcript"] = False
        result = dp.postflight(report)
        self.assertIs(result["verified"], False)
        self.assertIn("text_arrived", result["failed"])

    def test_postflight_never_claims_to_own_the_live_path(self):
        result = dp.postflight({})
        self.assertTrue(result["advisory_only"])
        self.assertIn("commander", result["note"])


class BoundaryTests(unittest.TestCase):
    def test_module_is_read_only_socket_free_and_loop_free(self):
        text = SRC.read_text(encoding="utf-8")
        tree = ast.parse(text)
        banned = {"socket", "urllib", "requests", "httpx", "subprocess", "threading",
                  "asyncio", "multiprocessing", "insight_proposer", "local_model_session"}
        scanned = 0
        for node in ast.walk(tree):
            self.assertFalse(isinstance(node, ast.While), "no resident loop")
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] + [a.name for a in node.names]
                     if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                scanned += 1
                with self.subTest(name=name):
                    self.assertFalse(any(name == bad or name.startswith(bad + ".")
                                         for bad in banned))
        self.assertGreater(scanned, 0)
        for forbidden in ("pwsh", "PowerShell", "SendInput", "SetValue", "-Mode deliver"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)
        self.assertNotIn("build_parser", text)
        self.assertNotIn("add_parser", text)

    def test_run_reports_scan_volume_and_hard_boundaries(self):
        root, holder = _root()
        try:
            report = dp.run(root)
            self.assertEqual(report["scanned"], 1)
            self.assertEqual(len(report["agents"]), 1)
            for key in ("models_loaded", "processes_started"):
                self.assertEqual(report[key], 0)
            for key in ("ui_input_performed", "deployment_performed",
                        "system_tasks_registered"):
                self.assertIs(report[key], False)
        finally:
            holder.cleanup()

    def test_capability_card_and_boundary_doc_exist(self):
        card = REPO / "skills" / "aep-delivery-preflight" / "SKILL.md"
        doc = REPO / "docs" / "DESKTOP_DELIVERY_PREFLIGHT.zh-CN.md"
        self.assertTrue(card.is_file())
        self.assertTrue(doc.is_file())
        text = card.read_text(encoding="utf-8") + doc.read_text(encoding="utf-8")
        for token in ("只读", "session_marker", "task_delivered", "第二意见",
                      "action_verification", "delivery_verdict", "UIPI"):
            with self.subTest(token=token):
                self.assertIn(token, text)


class DispatchReadinessTests(unittest.TestCase):
    """The three cardpoints found by real dispatch on 2026-11-22, frozen."""

    def test_two_event_ids_are_refused_before_any_input(self):
        # The WorkBuddy AI order carried an auto EVT- id plus a hand-written
        # WAKE- id and was refused at one_event_id (nothing was typed).
        body = "派工 EVT-FB7E4FA30D 回执 WAKE-DESKTOP-WBAI-20261122"
        report = dp.dispatch_text_diagnosis(body, event_id="EVT-FB7E4FA30D")
        self.assertIn("ONE_EVENT_ID_EXPECTED", report["issues"])
        self.assertFalse(report["may_dispatch"])
        self.assertEqual(len(report["event_ids_found"]), 2)
        self.assertIs(report["ui_input_performed"], False)

    def test_single_event_id_with_ack_passes(self):
        body = ("派工 EVT-79DA9A8BB 请回报能力 [AEP-CMD-DRILL] "
                "回复请带 AEP-ACK")
        report = dp.dispatch_text_diagnosis(body, event_id="EVT-79DA9A8BB")
        self.assertEqual(report["issues"], [])
        self.assertTrue(report["may_dispatch"])
        self.assertTrue(report["ack_instruction"])

    def test_expected_id_mismatch_is_named(self):
        body = "派工 EVT-111111 回执 AEP-ACK"
        report = dp.dispatch_text_diagnosis(body, event_id="EVT-222222")
        self.assertIn("EVENT_ID_MISMATCH", report["issues"])
        self.assertFalse(report["may_dispatch"])

    def test_session_marker_drift_is_named_with_suggestion(self):
        # qoder's marker was a sidebar button absent from the chat.  The fix
        # is a config correction, never a relaxation of seen=True.
        profile = {"session_marker": "请加入项目协作区",
                   "session_evidence_markers": ["evolution-protocol", "加入项目协作区"]}
        transcript = "agent-evolution-protocol 会话 agent-evolution-protocol"
        observe = {"session_marker_check": "absent", "session_marker_seen": False}
        report = dp.session_marker_diagnosis(
            profile, transcript=transcript, observe=observe)
        self.assertTrue(report["config_drift_suspected"])
        self.assertIn("SESSION_MARKER_NOT_IN_TRANSCRIPT", report["issues"])
        self.assertIn("evolution-protocol", report["suggested_markers"])
        self.assertIs(report["gate_was_relaxed"], False)

    def test_fixed_session_marker_has_no_drift(self):
        profile = {"session_marker": "agent-evolution-protocol",
                   "session_evidence_markers": ["evolution-protocol"]}
        report = dp.session_marker_diagnosis(
            profile, transcript="agent-evolution-protocol 会话",
            observe={"session_marker_check": "seen", "session_marker_seen": True})
        self.assertEqual(report["issues"], [])
        self.assertFalse(report["config_drift_suspected"])

    def test_readiness_needs_profile_and_names_action(self):
        root, holder = _root(profile=False)
        try:
            report = dp.dispatch_readiness(root, AGENT)
            self.assertEqual(report["action"], "NEED_PROFILE")
            self.assertIn("ORIGINAL_SESSION_PROFILE_REQUIRED", report["blockers"])
            self.assertFalse(report["may_dispatch"])
            self.assertIs(report["ui_input_performed"], False)
        finally:
            holder.cleanup()

    def test_readiness_combines_text_blocker(self):
        root, holder = _root()
        try:
            body = "EVT-FB7E4FA30D WAKE-DESKTOP-WBAI-20261122 AEP-ACK"
            report = dp.dispatch_readiness(root, AGENT, text=body,
                                           event_id="EVT-FB7E4FA30D")
            self.assertEqual(report["action"], "FIX_DISPATCH_TEXT")
            self.assertIn("ONE_EVENT_ID_EXPECTED", report["blockers"])
            self.assertFalse(report["may_dispatch"])
        finally:
            holder.cleanup()

    def test_stale_observe_is_never_dispatchable(self):
        # qoder_cn had a historical "seen" receipt on disk while no window
        # existed at all; the scan must not call that ready.
        root, holder = _root()
        try:
            report = dp.dispatch_readiness(root, AGENT)
            self.assertTrue(report["observe_freshness"]["fresh"])
            self.assertEqual(report["action"], "SEND_AFTER_VERIFICATION")
            # Force staleness with an explicit clock without touching files.
            stale = dp.observe_freshness(root, AGENT, now=10 ** 12)
            self.assertFalse(stale["fresh"])
            self.assertEqual(stale["reason"], "OBSERVE_STALE")
        finally:
            holder.cleanup()

    def test_stale_receipt_blocks_dispatch_readiness(self):
        root, holder = _root()
        try:
            receipt = root / "uia_receipts" / f"last-{AGENT}.json"
            import os
            os.utime(receipt, (0, 0))
            report = dp.dispatch_readiness(root, AGENT)
            self.assertIn("OBSERVE_STALE", report["blockers"])
            self.assertEqual(report["action"], "REOBSERVE_FIRST")
            self.assertFalse(report["may_dispatch"])
        finally:
            holder.cleanup()

    def test_readiness_cli_is_read_only(self):
        code = SRC.read_text(encoding="utf-8")
        self.assertIn("--readiness", code)
        self.assertIn("--text", code)
        self.assertNotIn("build_parser", code)
        self.assertNotIn("add_parser", code)


class RealDispatchCardpointTests(unittest.TestCase):
    """Three cardpoints measured on 2026-11-22, kept advisory and read-only."""

    def test_background_ocr_gap_is_named_without_loosening_the_gate(self):
        report = dp.background_capture_diagnosis(
            {"ocr_skipped_reason": "BACKGROUND_CAPTURE_UNVERIFIED",
             "transcript_source": "uia-background-unreadable",
             "transcript_chars": 0},
            capture="加入工作区协作" * 10)
        self.assertTrue(report["background_capture_gap"])
        self.assertTrue(report["foreground_capture_readable"])
        self.assertFalse(report["gate_was_relaxed"])
        self.assertFalse(report["ui_input_performed"])
        self.assertTrue(report["advisory_only"])

    def test_a_readable_background_is_not_falsely_labelled_a_gap(self):
        report = dp.background_capture_diagnosis(
            {"ocr_skipped_reason": "", "transcript_source": "uia",
             "transcript_chars": 12000})
        self.assertFalse(report["background_capture_gap"])
        self.assertFalse(report["foreground_capture_readable"])

    def test_active_lease_is_named_and_requires_original_session_evidence(self):
        from agent_evolution_protocol.coordination import session_lease
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "coordination"
            lease_root = session_lease.canonical_root(root)
            value = session_lease.binding(
                workspace_id="ws", provider="uia", target_agent="workbuddy3",
                session_id="加入工作区协作")
            with session_lease.acquire(lease_root, value, timeout=5) as lease:
                lease.write(event_id="EVT-OLD", state="UNCERTAIN",
                            reason="submitted-unconfirmed")
            report = dp.session_lease_diagnosis(root, "workbuddy3", binding=value)
            self.assertEqual(report["state"], "UNCERTAIN")
            self.assertTrue(report["is_active"])
            self.assertTrue(report["requires_original_session_evidence"])
            self.assertIn("session_lease.reconcile", report["remedy"])
            self.assertFalse(report["ui_input_performed"])

    def test_client_error_beside_visible_message_is_not_ack(self):
        report = dp.postflight(
            {"status": "message-visible", "task_delivered": True,
             "probe_in_transcript": True},
            transcript="事件已显示 WorkBuddy 已完成 发生未知错误暂无响应")
        response = report["client_response"]
        self.assertTrue(response["client_error_reported"])
        self.assertIn("发生未知错误", response["error_markers"])
        self.assertFalse(response["implies_ack"])
        self.assertFalse(response["may_advance"])
        self.assertTrue(response["advisory_only"])

    def test_no_error_marker_keeps_client_response_clean(self):
        report = dp.postflight(
            {"status": "message-visible", "task_delivered": True},
            transcript="事件已显示 对方已接单")
        self.assertFalse(report["client_response"]["client_error_reported"])


if __name__ == "__main__":
    unittest.main()
