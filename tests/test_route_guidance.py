from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agent_evolution_protocol import cli
from agent_evolution_protocol.route_guidance import build_next_action


class RouteGuidanceTests(unittest.TestCase):
    def packet(self, **updates):
        return {"scenario": "task-join", "agent": "fixture-agent", "task": "T125",
            "recovery": {"status": "unclaimed"}, "requires_clarification": False,
            "ambiguity": False, "blockers": [], **updates}

    def test_arguments_preserve_spaces_quotes_and_chinese_without_shell_parsing(self):
        workspace = "K:/中文 空间/owner's project/\"quoted\""
        coordination = "K:/协作 空间/it's \"data\"; $(echo nope)"
        packet = self.packet(first_commands=["aep something <TASK>; dangerous-command"])
        original = deepcopy(packet)
        with patch("shlex.split", side_effect=AssertionError("shell parsing is forbidden")):
            step = build_next_action(packet, root=workspace, coord_root=coordination)
        self.assertEqual(step["argv"], ["aep", "--coord-root", coordination,
                                       "coord-digest", "--agent", "fixture-agent"])
        self.assertEqual(step["cwd"], workspace)
        self.assertFalse(step["shell"])
        self.assertFalse(step["execution_started"])
        self.assertEqual(step["placeholders"], [])
        self.assertEqual(json.loads(json.dumps(step, ensure_ascii=False))["argv"], step["argv"])
        self.assertEqual(packet, original)

    def test_missing_workspace_coord_root_and_task_never_use_defaults(self):
        for root, croot, packet, field in (
            (None, "coord", self.packet(), "workspace"),
            ("workspace", None, self.packet(), "coord_root"),
            ("workspace", "coord", self.packet(task=""), "task"),
        ):
            with self.subTest(field=field):
                step = build_next_action(packet, root=root, coord_root=croot)
                self.assertEqual(step["kind"], "needs_input")
                self.assertIsNone(step["argv"])
                self.assertIn(field, [item["field"] for item in step["placeholders"]])
                self.assertIsNone(step["fallback"]["requested_argv"])

    def test_ambiguous_or_unrecognized_state_requires_input(self):
        for packet, field in ((self.packet(ambiguity=True), "intent_scope"),
                              (self.packet(recovery={"status": "future-state"}), "recovery.status"),
                              (self.packet(recovery=None), "recovery.status"),
                              (self.packet(scenario="future-route"), "scenario")):
            with self.subTest(field=field):
                step = build_next_action(packet, root="workspace", coord_root="coord")
                self.assertIsNone(step["argv"])
                self.assertIn(field, [item["field"] for item in step["placeholders"]])

    def test_unknown_claim_only_offers_status_read_preserving_blockers(self):
        packet = self.packet(recovery={"status": "unknown"},
                             blockers=["UNKNOWN external operation; inspect before retry"])
        step = build_next_action(packet, root="workspace", coord_root="coord")
        self.assertEqual(step["argv"], ["aep", "--coord-root", "coord", "coord-status"])
        self.assertTrue(step["state_resolution_required"])
        self.assertEqual(step["blockers"], packet["blockers"])
        self.assertIsNot(step["blockers"], packet["blockers"])

    def test_file_only_contains_explicit_unexecuted_manual_request(self):
        step = build_next_action(self.packet(scenario="cli-less-fallback"), root="workspace", coord_root="coord")
        self.assertEqual(step["kind"], "file_only")
        self.assertIsNone(step["argv"])
        self.assertEqual(step["required_capability"], "file_read")
        self.assertEqual(step["fallback"]["requested_argv"], ["aep", "--coord-root", "coord", "coord-status"])
        self.assertFalse(step["fallback"]["automatic_dispatch"])
        self.assertFalse(step["fallback"]["files_verified"])
        self.assertIn(str(Path("workspace") / "AEP_JOIN.md"), step["fallback"]["files"])

    def test_invalid_identifiers_and_missing_wake_target_are_not_substituted(self):
        for packet, field in ((self.packet(task="../other"), "task"),
                              (self.packet(task="CON"), "task"),
                              (self.packet(agent="--other"), "agent"),
                              (self.packet(scenario="wake-agent", target_agent=""), "target_agent")):
            with self.subTest(field=field):
                step = build_next_action(packet, root="workspace", coord_root="coord")
                self.assertIsNone(step["argv"])
                self.assertIn(field, step["fallback"]["missing_inputs"])

    def test_generated_commands_parse_and_execute_read_only_without_creating_roots(self):
        with TemporaryDirectory(prefix="aep guidance 中文 ") as temporary:
            root = Path(temporary)
            coord = root / "owner's coordination space"
            for scenario in ("task-join", "status-report", "review-only", "takeover",
                             "shared-resource-lock", "isolated-exploration", "stalled-recovery"):
                with self.subTest(scenario=scenario):
                    step = build_next_action(self.packet(scenario=scenario), root=root, coord_root=coord)
                    self.assertEqual(step["kind"], "command")
                    args = cli.build_parser().parse_args(step["argv"][1:])
                    with redirect_stdout(StringIO()) as output:
                        args.func(args)
                    self.assertIsInstance(json.loads(output.getvalue()), dict)
                    self.assertEqual(list(root.iterdir()), [])

    def test_builder_never_probes_or_executes_even_for_external_write_route(self):
        with patch("subprocess.run", side_effect=AssertionError("must not execute")), \
                patch("pathlib.Path.mkdir", side_effect=AssertionError("must not write")), \
                patch("pathlib.Path.exists", side_effect=AssertionError("must not infer")):
            step = build_next_action(self.packet(scenario="shared-resource-lock"),
                                     root="workspace", coord_root="coord")
        self.assertTrue(step["read_only"])
        self.assertEqual(step["argv"][-1], "coord-status")


if __name__ == "__main__":
    unittest.main()
