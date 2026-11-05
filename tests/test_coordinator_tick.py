"""Acceptance gate for the one-shot coordinator tick.

The coordinator is a *wiring layer*, not a scheduler.  Three single-step chains
already exist: `campaign_tick.tick()` (settlement/deadlines/presence/mailbox),
`mission_pulse.pulse()` (persisted missions -> DEPOSITED orders) and
`federation/supervisor.step()` (DAG settle -> schedule -> lease -> dispatch,
which already calls `commander.tick()` internally).

These tests are written before the implementation so no shortcut can turn a
thin wiring layer into a fourth scheduler, duplicate a dispatch, or quietly
start a daemon / model / process.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agent_evolution_protocol.coordination import coordinator_tick as ct

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "src" / "agent_evolution_protocol" / "coordination" / "coordinator_tick.py"


def _stub_steps(*, campaign=None, mission=None, commander=None, supervisor=None):
    """Patch the four existing single steps; return the patchers' mocks."""
    return (
        mock.patch.object(ct.campaign_tick, "tick",
                          side_effect=campaign if campaign is not None else (lambda *a, **k: {})),
        mock.patch.object(ct.mission_pulse, "pulse",
                          side_effect=mission if mission is not None else (lambda *a, **k: {})),
        mock.patch.object(ct.commander, "tick",
                          side_effect=commander if commander is not None else (lambda *a, **k: {})),
        mock.patch("agent_evolution_protocol.federation.supervisor.step",
                   side_effect=supervisor if supervisor is not None else (lambda *a, **k: {})),
        mock.patch("agent_evolution_protocol.federation.supervisor.build_adapters",
                   side_effect=lambda *a, **k: []),
    )


class DryRunTests(unittest.TestCase):
    def test_empty_root_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = ct.tick(root)
            self.assertEqual(list(root.rglob("*")), [])
        self.assertFalse(report["applied"])
        self.assertEqual(report["writes_performed"], 0)
        self.assertEqual(report["dispatch_path"], "none")
        self.assertEqual(report["halted"], "")
        self.assertIn("preview", report)
        self.assertIn("plan", report)

    def test_dry_run_never_calls_writing_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            campaigns, missions, commanders, steps, adapters = _stub_steps()
            with campaigns as c, missions as m, commanders as d, steps as s, adapters as a:
                ct.tick(Path(tmp))
                c.assert_not_called()
                d.assert_not_called()
                s.assert_not_called()
                a.assert_not_called()
                # The read-only preview is allowed to reuse the mission scanner,
                # but only in its non-writing mode.
                m.assert_called()
                for call in m.call_args_list:
                    self.assertFalse(call.kwargs.get("apply", False),
                                     "a dry-run must never pass apply=True")

    def test_preview_reports_all_read_only_sources_and_next_due(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = ct.tick(Path(tmp))
        preview = report["preview"]
        for key in ("mission", "campaign", "presence", "commander", "next_due_at"):
            with self.subTest(key=key):
                self.assertIn(key, preview)
        self.assertEqual(preview["mission"]["scanned"], 0)
        self.assertEqual(preview["next_due_at"], "")
        self.assertEqual(preview["next_due_source"], "campaign_tick.next_due")

    def test_plan_lists_existing_steps_in_fixed_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = ct.tick(Path(tmp))
        stages = [row["stage"] for row in report["plan"]]
        self.assertEqual(stages[:3], ["settle_and_deadlines", "mission_intake", "dispatch"])
        for row in report["plan"]:
            with self.subTest(stage=row["stage"]):
                self.assertIn("callable", row)
                self.assertIn("writes", row)
        self.assertTrue(any("federation" in row["callable"] or "commander" in row["callable"]
                            for row in report["plan"]))


class ApplyOrderTests(unittest.TestCase):
    def test_apply_calls_existing_steps_in_fixed_order(self):
        calls = []

        def campaign(*a, **k):
            calls.append("campaign")
            return {}

        def mission(*a, **k):
            calls.append("mission")
            self.assertTrue(k.get("apply"), "mission intake must actually queue in --apply mode")
            return {}

        def commander(*a, **k):
            calls.append("dispatch")
            return {}

        with tempfile.TemporaryDirectory() as tmp:
            campaigns, missions, commanders, steps, adapters = _stub_steps(
                campaign=campaign, mission=mission, commander=commander)
            with campaigns, missions, commanders, steps, adapters:
                report = ct.tick(Path(tmp), apply=True)
            self.assertEqual(calls, ["campaign", "mission", "dispatch"])
            self.assertEqual(report["dispatch_path"], "commander_tick")
            self.assertEqual(report["writes_performed"], 3)
            self.assertEqual([row["status"] for row in report["stages"]], ["ok", "ok", "ok"])
            self.assertTrue(report["applied"])

    def test_settle_failure_halts_before_mission_and_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            campaigns, missions, commanders, steps, adapters = _stub_steps(
                campaign=lambda *a, **k: (_ for _ in ()).throw(OSError("disk unreadable")))
            with campaigns, missions as m, commanders as d, steps, adapters:
                report = ct.tick(Path(tmp), apply=True)
                m.assert_not_called()
                d.assert_not_called()
            self.assertEqual(report["halted"], "SETTLE_FAILED")
            self.assertEqual(report["dispatch_path"], "none")
            self.assertEqual(report["stages"][0]["status"], "failed")
            self.assertEqual(report["writes_performed"], 0)

    def test_mission_failure_halts_before_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            campaigns, missions, commanders, steps, adapters = _stub_steps(
                mission=lambda *a, **k: (_ for _ in ()).throw(ValueError("bad candidate")))
            with campaigns, missions, commanders as d, steps, adapters:
                report = ct.tick(Path(tmp), apply=True)
                d.assert_not_called()
            self.assertEqual(report["halted"], "MISSION_FAILED")
            self.assertEqual(report["dispatch_path"], "none")
            self.assertEqual(report["writes_performed"], 1)

    def test_no_federation_context_is_skipped_explicitly_not_reported_as_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            campaigns, missions, commanders, steps, adapters = _stub_steps()
            with campaigns, missions, commanders as d, steps as s, adapters:
                report = ct.tick(Path(tmp), apply=True)
                d.assert_called_once()
                s.assert_not_called()
            self.assertEqual(report["dispatch_path"], "commander_tick")
            skipped = report["skipped"]
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0]["stage"], "federation")
            self.assertEqual(skipped[0]["reason"], "FEDERATION_CONTEXT_MISSING")
            self.assertEqual([row["status"] for row in report["stages"]], ["ok", "ok", "ok"])

    def test_federation_context_uses_supervisor_step_and_never_double_dispatches(self):
        with tempfile.TemporaryDirectory() as tmp:
            campaigns, missions, commanders, steps, adapters = _stub_steps()
            with campaigns, missions, commanders as d, steps as s, adapters as a:
                report = ct.tick(Path(tmp), apply=True, kernel_root=Path(tmp) / "kernel",
                                 workspace_id="T1", agents=["codex", "qoder"])
                d.assert_not_called()
                s.assert_called_once()
                a.assert_called_once()
            self.assertEqual(report["dispatch_path"], "federation_step")
            self.assertFalse(any(row["status"] == "skipped" for row in report["stages"]))

    def test_federation_failure_never_falls_back_to_a_second_dispatch(self):
        """supervisor.step() may already have sent; retrying here duplicates delivery."""
        with tempfile.TemporaryDirectory() as tmp:
            campaigns, missions, commanders, steps, adapters = _stub_steps(
                supervisor=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("state missing")))
            with campaigns, missions, commanders as d, steps, adapters:
                report = ct.tick(Path(tmp), apply=True, kernel_root=Path(tmp) / "kernel",
                                 workspace_id="T1", agents=["codex"])
                d.assert_not_called()
            self.assertEqual(report["dispatch_path"], "federation_step")
            self.assertEqual(report["stages"][-1]["status"], "failed")
            self.assertNotIn("fallback", report)


class BoundaryTests(unittest.TestCase):
    def test_receipt_hard_boundaries_are_always_zero_or_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            dry = ct.tick(Path(tmp))
            campaigns, missions, commanders, steps, adapters = _stub_steps()
            with campaigns, missions, commanders, steps, adapters:
                applied = ct.tick(Path(tmp), apply=True)
        for report in (dry, applied):
            for key in ("models_loaded", "processes_started"):
                with self.subTest(key=key):
                    self.assertEqual(report[key], 0)
            for key in ("ui_touched", "deployment_performed", "system_tasks_registered"):
                with self.subTest(key=key):
                    self.assertIs(report[key], False)
            self.assertIn("note", report)

    def test_module_stays_socket_free_process_free_and_model_free(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        banned = {"socket", "urllib", "requests", "httpx", "subprocess", "threading",
                  "asyncio", "multiprocessing", "insight_proposer", "local_model_session",
                  "insight_miner", "mission_graph", "time"}
        seen = 0
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [alias.name for alias in node.names]
            for name in names:
                seen += 1
                with self.subTest(name=name):
                    self.assertFalse(any(name == bad or name.startswith(bad + ".")
                                         for bad in banned))
        self.assertGreater(seen, 0, "the AST guard must actually scan imports")

    def test_no_loop_no_sleep_no_second_state_machine(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, ast.While) for node in ast.walk(tree)),
                         "a resident loop would turn a tick into a daemon")
        calls = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)]
        for func in calls:
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            self.assertNotIn(name, {"sleep", "serve", "watch", "Popen", "run"})

    def test_json_cli_output_is_parseable_and_defaults_to_dry_run(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with mock.patch("sys.argv", ["coordinator_tick", "--coord-root", tmp]), \
                    redirect_stdout(buf):
                self.assertEqual(ct.main(), 0)
            payload = json.loads(buf.getvalue())
        self.assertFalse(payload["applied"])
        self.assertEqual(payload["writes_performed"], 0)

    def test_capability_card_and_boundary_doc_exist(self):
        card = REPO / "skills" / "aep-coordinator-tick" / "SKILL.md"
        doc = REPO / "docs" / "COORDINATOR_TICK.zh-CN.md"
        self.assertTrue(card.is_file())
        self.assertTrue(doc.is_file())
        for token in ("零模型", "不是第二套调度器", "不回退", "DEPOSITED", "只有 `VERIFIED` 能关单"):
            with self.subTest(token=token):
                self.assertIn(token, card.read_text(encoding="utf-8")
                              + doc.read_text(encoding="utf-8"))

    def test_trigger_script_is_ascii_and_never_defaults_to_apply(self):
        script = REPO / "scripts" / "run-coordinator-tick.ps1"
        self.assertTrue(script.is_file())
        raw = script.read_bytes()
        raw.decode("ascii")
        self.assertIn('if ($Apply)', raw.decode("ascii"))
        self.assertNotIn('$Apply = $true', raw.decode("ascii"))


if __name__ == "__main__":
    unittest.main()
