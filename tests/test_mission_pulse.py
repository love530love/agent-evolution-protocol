"""Acceptance gate for the model-free mission pulse bridge.

The bridge joins `missions/M-*.json` to the *existing* commander queue.  It must
never become a second scheduler, never need a model, never start a process, and
never touch a desktop.  These tests are written before the implementation so a
convenient shortcut cannot quietly widen the boundary.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from agent_evolution_protocol.coordination import mission_pulse as pulse

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 5, 5, 0, tzinfo=timezone.utc)
MEMBERS = {"codex", "qoder_cn", "workbuddyai"}


def _mission(*, mission_id="M-test00000001", actor="codex", stage_state="ready",
             deadline=None, state="running", extra_stages=None) -> dict:
    stage = {"stage_id": "implementation", "state": stage_state, "depends_on": [],
             "description": "build the accepted candidate", "updated_at": "2026-11-05T04:00:00+00:00"}
    if actor is not None:
        stage["actor"] = actor
    return {
        "mission_id": mission_id,
        "goal": "prove the mission-to-commander bridge",
        "workspace": "K:/tmp",
        "workspace_id": "",
        "deadline": deadline or (NOW + timedelta(days=2)).isoformat(),
        "budget": "", "deliverables": [], "constraints": [],
        "state": state, "created_at": "2026-11-05T03:00:00+00:00",
        "stages": [stage] + list(extra_stages or []),
    }


def _root(**kwargs) -> tuple[Path, Path]:
    holder = tempfile.TemporaryDirectory()
    root = Path(holder.name)
    root.joinpath("missions").mkdir(parents=True)
    path = root / "missions" / f"{kwargs.get('mission_id', 'M-test00000001')}.json"
    mission = _mission(**kwargs)
    path.write_text(json.dumps(mission, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return root, path, holder


def _pulse(root, **kwargs):
    return pulse.pulse(root, now=NOW, member_names=MEMBERS, **kwargs)


def _orders(root) -> list[dict]:
    folder = Path(root) / "commander" / "inbox"
    if not folder.is_dir():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


class ScanTests(unittest.TestCase):
    def test_empty_root_still_reports_scan_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = pulse.scan(Path(tmp), now=NOW, member_names=MEMBERS)
        self.assertEqual(report["scanned"], 0)
        self.assertEqual(report["candidates"], [])
        self.assertEqual(report["rejected"], [])
        self.assertIn("actions", report)
        self.assertIn("member_names", report)

    def test_unreadable_mission_is_reported_not_silently_dropped(self):
        root, path, holder = _root()
        try:
            path.write_text("{not json", encoding="utf-8")
            report = pulse.scan(root, now=NOW, member_names=MEMBERS)
            self.assertEqual(report["scanned"], 1)
            self.assertEqual(report["rejected"][0]["reason"], "MISSION_UNREADABLE")
        finally:
            holder.cleanup()

    def test_assign_action_produces_one_candidate_with_a_stable_event_id(self):
        root, _, holder = _root()
        try:
            report = pulse.scan(root, now=NOW, member_names=MEMBERS)
            self.assertEqual(report["scanned"], 1)
            self.assertEqual(report["actions"].get("assign"), 1)
            self.assertEqual(len(report["candidates"]), 1)
            cand = report["candidates"][0]
            self.assertEqual(cand["action"], "assign")
            self.assertEqual(cand["stage_id"], "implementation")
            self.assertEqual(cand["target"], "codex")
            self.assertTrue(cand["event_id"].startswith("PULSE-"))
            self.assertEqual(cand["event_id"],
                             pulse.event_id_for(json.loads(
                                 (root / "missions" / "M-test00000001.json").read_text(encoding="utf-8")),
                                 {"stage_id": "implementation", "state": "ready",
                                  "updated_at": "2026-11-05T04:00:00+00:00"}))
        finally:
            holder.cleanup()

    def test_non_assign_actions_are_reported_and_never_queued(self):
        cases = {
            "running": "observe",
            "failed": "recovery_review",
            "blocked": "blocked_review",
        }
        for stage_state, expected in cases.items():
            with self.subTest(stage_state=stage_state):
                root, _, holder = _root(stage_state=stage_state)
                try:
                    report = pulse.scan(root, now=NOW, member_names=MEMBERS)
                    self.assertEqual(report["actions"].get(expected), 1)
                    self.assertEqual(report["candidates"], [])
                    self.assertTrue(any(r["reason"] == "ACTION_NOT_ASSIGNABLE"
                                        for r in report["rejected"]))
                finally:
                    holder.cleanup()

    def test_completed_mission_reports_delivery_without_a_candidate(self):
        root, _, holder = _root(state="completed")
        try:
            report = pulse.scan(root, now=NOW, member_names=MEMBERS)
            self.assertEqual(report["actions"].get("report_delivery"), 1)
            self.assertEqual(report["candidates"], [])
        finally:
            holder.cleanup()


class GateTests(unittest.TestCase):
    def _reasons(self, **kwargs) -> list[str]:
        root, _, holder = _root(**kwargs)
        try:
            report = pulse.scan(root, now=NOW, member_names=MEMBERS)
            return [reason for row in report["rejected"] for reason in row.get("reasons", [])]
        finally:
            holder.cleanup()

    def test_unknown_agent_is_refused(self):
        self.assertIn("UNKNOWN_AGENT", self._reasons(actor="ghost_agent"))

    def test_missing_actor_is_refused(self):
        self.assertIn("NO_TARGET", self._reasons(actor=None))

    def test_invalid_agent_name_is_refused(self):
        self.assertIn("INVALID_AGENT_NAME", self._reasons(actor="Not Valid"))

    def test_past_deadline_is_refused(self):
        self.assertIn("MISSION_PAST_DEADLINE",
                      self._reasons(deadline="2026-10-30T00:00:00+08:00"))

    def test_human_required_and_waiting_are_refused(self):
        root, _, holder = _root()
        try:
            folder = root / "task_states"
            folder.mkdir(parents=True)
            (folder / "M-test00000001.json").write_text(json.dumps({
                "task": "M-test00000001", "owner": "codex", "phase": "working",
                "human_required": True, "next_action": "", "at": NOW.isoformat(),
            }), encoding="utf-8")
            reasons = [r for row in pulse.scan(root, now=NOW, member_names=MEMBERS)["rejected"]
                       for r in row.get("reasons", [])]
            self.assertIn("HUMAN_REQUIRED", reasons)
            self.assertEqual(pulse.scan(root, now=NOW, member_names=MEMBERS)["candidates"], [])
        finally:
            holder.cleanup()

    def test_waiting_and_terminal_phases_are_refused(self):
        for phase, expected in (("waiting", "WAITING_FOR_EVENT"),
                                ("done", "TASK_TERMINAL"),
                                ("abandoned", "TASK_TERMINAL")):
            with self.subTest(phase=phase):
                root, _, holder = _root()
                try:
                    folder = root / "task_states"
                    folder.mkdir(parents=True)
                    (folder / "M-test00000001.json").write_text(json.dumps({
                        "task": "M-test00000001", "owner": "codex", "phase": phase,
                        "human_required": False, "at": NOW.isoformat(),
                    }), encoding="utf-8")
                    reasons = [r for row in pulse.scan(root, now=NOW, member_names=MEMBERS)["rejected"]
                               for r in row.get("reasons", [])]
                    self.assertIn(expected, reasons)
                finally:
                    holder.cleanup()

    def test_claim_owner_mismatch_is_refused_not_taken_over(self):
        root, _, holder = _root()
        try:
            folder = root / "claims" / "M-test00000001"
            folder.mkdir(parents=True)
            (folder / "owner.json").write_text(json.dumps({
                "task": "M-test00000001", "agent": "somebody_else",
                "expires_at": (NOW + timedelta(hours=2)).isoformat(),
            }), encoding="utf-8")
            report = pulse.scan(root, now=NOW, member_names=MEMBERS)
            self.assertEqual(report["candidates"], [])
            self.assertIn("CLAIM_OWNER_MISMATCH",
                          [r for row in report["rejected"] for r in row.get("reasons", [])])
        finally:
            holder.cleanup()

    def test_expired_claim_is_refused(self):
        root, _, holder = _root()
        try:
            folder = root / "claims" / "M-test00000001"
            folder.mkdir(parents=True)
            (folder / "owner.json").write_text(json.dumps({
                "task": "M-test00000001", "agent": "codex",
                "expires_at": (NOW - timedelta(minutes=1)).isoformat(),
            }), encoding="utf-8")
            report = pulse.scan(root, now=NOW, member_names=MEMBERS)
            self.assertEqual(report["candidates"], [])
            self.assertIn("CLAIM_EXPIRED",
                          [r for row in report["rejected"] for r in row.get("reasons", [])])
        finally:
            holder.cleanup()

    def test_already_queued_event_is_idempotent(self):
        root, _, holder = _root()
        try:
            event_id = pulse.scan(root, now=NOW, member_names=MEMBERS)["candidates"][0]["event_id"]
            folder = root / "commander" / "inbox"
            folder.mkdir(parents=True)
            (folder / "2026-11-05T040000000000+0000-abcdef12.json").write_text(
                json.dumps({"id": "old", "event_id": event_id, "state": "DEPOSITED"}),
                encoding="utf-8")
            report = pulse.scan(root, now=NOW, member_names=MEMBERS)
            self.assertEqual(report["candidates"], [])
            self.assertIn("ALREADY_QUEUED",
                          [r for row in report["rejected"] for r in row["reasons"]])
        finally:
            holder.cleanup()


class PulseTests(unittest.TestCase):
    def test_dry_run_writes_nothing_and_reports_the_hard_limits(self):
        root, path, holder = _root()
        try:
            before = path.read_bytes()
            report = _pulse(root)
            self.assertFalse(report["applied"])
            self.assertEqual(report["candidates"], 1)
            self.assertEqual(report["queued"], [])
            self.assertEqual(_orders(root), [])
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((root / "commander").exists())
            for key in ("models_loaded", "processes_started", "ui_touched",
                        "deployment_performed", "system_tasks_registered"):
                with self.subTest(key=key):
                    self.assertFalse(report[key])
            self.assertIn("note", report)
        finally:
            holder.cleanup()

    def test_apply_queues_exactly_one_deposited_order(self):
        root, path, holder = _root()
        try:
            before = path.read_bytes()
            report = _pulse(root, apply=True)
            self.assertTrue(report["applied"])
            self.assertEqual(len(report["queued"]), 1)
            orders = _orders(root)
            self.assertEqual(len(orders), 1)
            order = orders[0]
            self.assertEqual(order["state"], "DEPOSITED")
            self.assertEqual(order["target"], "codex")
            self.assertEqual(order["sender"], pulse.DEFAULT_SENDER)
            self.assertEqual(order["task"], "M-test00000001")
            self.assertEqual(order["event_id"], report["queued"][0]["event_id"])
            self.assertIn(order["event_id"], order["text"])
            self.assertIn("AEP-MISSION-PULSE", order["text"])
            self.assertEqual(path.read_bytes(), before, "the mission file is read-only to the pulse")
            self.assertFalse(report["ui_touched"])
        finally:
            holder.cleanup()

    def test_apply_twice_does_not_duplicate_the_order(self):
        root, _, holder = _root()
        try:
            first = _pulse(root, apply=True)
            second = _pulse(root, apply=True)
            self.assertEqual(len(first["queued"]), 1)
            self.assertEqual(second["queued"], [])
            self.assertEqual(len(_orders(root)), 1)
            self.assertTrue(any(r["reason"] == "ALREADY_QUEUED" for r in second["rejected"]))
        finally:
            holder.cleanup()

    def test_workspace_id_is_carried_into_the_order(self):
        root, path, holder = _root()
        try:
            mission = json.loads(path.read_text(encoding="utf-8"))
            from agent_evolution_protocol.coordination.workspace_identity import ensure_workspace_id
            mission["workspace_id"] = ensure_workspace_id({})["workspace_id"]
            path.write_text(json.dumps(mission, ensure_ascii=False), encoding="utf-8")
            report = _pulse(root, apply=True)
            self.assertEqual(report["queued"][0]["workspace_id"], mission["workspace_id"])
            self.assertEqual(_orders(root)[0]["workspace_id"], mission["workspace_id"])
        finally:
            holder.cleanup()

    def test_verify_refuses_a_mission_that_changed_after_the_scan(self):
        root, path, holder = _root()
        try:
            candidate = pulse.scan(root, now=NOW, member_names=MEMBERS)["candidates"][0]
            mission = json.loads(path.read_text(encoding="utf-8"))
            mission["goal"] = "silently changed after the scan"
            path.write_text(json.dumps(mission, ensure_ascii=False), encoding="utf-8")
            check = pulse.verify(candidate, now=NOW, member_names=MEMBERS)
            self.assertFalse(check["verified"])
            self.assertEqual(check["reason"], "mission-changed")
            # Stale evidence must never be allowed to write, even when the
            # changed file would itself pass a fresh scan.
            result = pulse.queue_candidate(root, candidate, now=NOW, member_names=MEMBERS)
            self.assertIsNone(result["queued"])
            self.assertEqual(result["reason"], "mission-changed")
            self.assertEqual(_orders(root), [])
            # A fresh external invocation re-scans and may legitimately queue
            # the new state: the rejection is of evidence, not of the file.
            fresh = _pulse(root, apply=True)
            self.assertEqual(len(fresh["queued"]), 1)
            self.assertNotEqual(fresh["queued"][0]["event_id"], candidate["event_id"])
        finally:
            holder.cleanup()

    def test_verify_refuses_a_forged_event_id(self):
        root, _, holder = _root()
        try:
            candidate = pulse.scan(root, now=NOW, member_names=MEMBERS)["candidates"][0]
            forged = {**candidate, "event_id": "PULSE-FORGED"}
            check = pulse.verify(forged, now=NOW, member_names=MEMBERS)
            self.assertFalse(check["verified"])
            self.assertEqual(check["reason"], "event-id-mismatch")
        finally:
            holder.cleanup()

    def test_event_id_is_stable_but_scoped_to_the_stage_state(self):
        mission = _mission()
        stage = mission["stages"][0]
        first = pulse.event_id_for(mission, stage)
        self.assertEqual(first, pulse.event_id_for(mission, stage))
        self.assertNotEqual(first, pulse.event_id_for(mission, {**stage, "state": "running"}))
        self.assertNotEqual(first, pulse.event_id_for({**mission, "mission_id": "M-other"}, stage))


class IsolationTests(unittest.TestCase):
    def test_module_stays_model_and_process_free(self):
        path = Path(pulse.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
                imported.update(alias.name for alias in node.names)
        self.assertGreater(len(imported), 6, "a vacuous import guard is not a guard")
        self.assertIn("pathlib", imported)
        for forbidden in ("socket", "urllib", "requests", "httpx", "subprocess",
                          "threading", "asyncio", "insight_proposer",
                          "local_model_session", "mission_graph"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, imported)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr,
                                 {"Popen", "run", "check_output", "system", "serve_forever"})

    def test_trigger_scripts_are_ascii_and_never_auto_register(self):
        run = REPO / "scripts" / "run-mission-pulse.ps1"
        reg = REPO / "scripts" / "register-mission-pulse-task.ps1"
        for script in (run, reg):
            with self.subTest(script=script.name):
                self.assertTrue(script.is_file())
                self.assertTrue(all(byte < 128 for byte in script.read_bytes()),
                                "PS 5.1 parses .ps1 as GBK; keep them pure ASCII")
        registration = reg.read_text(encoding="ascii")
        self.assertIn("[switch]$Register", registration)
        self.assertIn("if (-not $Register)", registration)
        head, tail = registration.split("if (-not $Register)", 1)
        self.assertNotIn("Register-ScheduledTask", head,
                         "registration must not run on the default path")
        self.assertIn("Register-ScheduledTask", tail)
        runner = run.read_text(encoding="ascii")
        self.assertIn("[switch]$Apply", runner)
        self.assertIn("if ($Apply)", runner)

    def test_capability_card_and_boundary_document_ship(self):
        card = REPO / "skills" / "aep-mission-pulse" / "SKILL.md"
        doc = REPO / "docs" / "LONGRUN_MISSION_PULSE.zh-CN.md"
        self.assertTrue(card.is_file())
        self.assertTrue(doc.is_file())
        text = card.read_text(encoding="utf-8")
        for token in ("model_required: false", "零模型", "--apply", "只有 `VERIFIED` 能关单"):
            with self.subTest(token=token):
                self.assertIn(token, text)
        for token in ("No Models Loaded", "692 MiB", "不是第二套调度器", "不注册系统任务"):
            with self.subTest(token=token):
                self.assertIn(token, doc.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
