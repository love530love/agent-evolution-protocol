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


if __name__ == "__main__":
    unittest.main()
