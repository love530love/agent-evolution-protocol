import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from agent_evolution_protocol import cli


class RouteCliTest(unittest.TestCase):
    def test_route_does_not_create_coord_or_kernel_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = cli.build_parser().parse_args(["route", "--agent", "new_agent", "--workspace", str(root), "--task", "web-fix-42", "--intent", "接入任务"])
            with redirect_stdout(StringIO()) as output:
                args.func(args)
            packet = json.loads(output.getvalue())
            self.assertEqual("WEB-FIX-42", packet["task"])
            self.assertEqual("USE_CURRENT_SESSION", packet["continuation_plan"]["action"])
            self.assertEqual([], list(root.iterdir()))

    def test_cross_directory_config_and_default_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / ".aep" / "workspace.json"
            config.parent.mkdir()
            config.write_text(json.dumps({"workspace": str(root), "coord_root": "shared coord", "kernel_root": "shared kernel", "default_task": "web-fix"}), encoding="utf-8")
            routing = config.parent / "routing.json"
            routing.write_text(json.dumps({"schema": "agent-evolution-routing-v1", "keywords": {"takeover": ["接棒"]}}), encoding="utf-8")
            args = cli.build_parser().parse_args(["route", "--agent", "new_agent", "--workspace", str(root), "--intent", "接棒"])
            with redirect_stdout(StringIO()) as output:
                args.func(args)
            packet = json.loads(output.getvalue())
            self.assertEqual(str(root / "shared coord"), packet["coord_root"])
            self.assertEqual("WEB-FIX", packet["task"])
            self.assertEqual("takeover", packet["scenario"])
            self.assertIn("接棒", packet["matched_rules"]["takeover"])
            self.assertFalse((root / "shared kernel").exists())

    def test_ambiguous_intent_suppresses_write_commands_and_claim(self):
        packet = json.loads(self.route("汇报 T125 状态，然后上传"))
        self.assertTrue(packet["requires_clarification"])
        self.assertFalse(packet["claim_guidance"]["claim_available"])
        self.assertFalse(any("kernel-lease-acquire" in command for command in packet["first_commands"]))

    def test_multiple_task_ids_require_scope(self):
        packet = json.loads(self.route("接手 T125 和 T126"))
        self.assertTrue(packet["requires_clarification"])
        self.assertFalse(packet["claim_guidance"]["claim_available"])

    def test_ids_reject_path_traversal_and_windows_devices(self):
        for value in ("../x", "a/b", "CON", "LPT1", "a;evil", ""):
            with self.subTest(value=value), self.assertRaises(cli.ProtocolError):
                cli.valid_task(value)

    def test_pending_claim_directory_is_not_advertised_free(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "claims" / "T125").mkdir(parents=True)
            self.assertFalse(cli.claim_guidance_for(root, "T125", "codex")["claim_available"])

    def route(self, intent, agent="codex", task="", markdown=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            out = StringIO()
            with redirect_stdout(out):
                cli.route_intent(SimpleNamespace(
                    coord_root=root / "coord",
                    kernel_root=root / "kernel",
                    agent=agent,
                    intent=intent,
                    task=task,
                    workspace=str(root),
                    markdown=markdown,
                ))
            return out.getvalue()

    def test_task_join_from_natural_language(self):
        packet = json.loads(self.route("请加入这个工作区协作，接入 T125"))

        self.assertEqual("task-join", packet["scenario"])
        self.assertEqual("T125", packet["task"])
        self.assertIn("claim-if-free", packet["allowed_actions"])
        self.assertIn("coord-claim", packet["claim_guidance"]["command"])

    def test_explicit_read_only_overrides_takeover(self):
        packet = json.loads(self.route("接手 T125 上传方案，只审查，不执行"))
        self.assertEqual("review-only", packet["scenario"])
        self.assertFalse(packet["claim_guidance"]["claim_available"])

    def test_project_join_has_no_claim(self):
        packet = json.loads(self.route("请加入这个工作区协作"))
        self.assertEqual("project-orientation", packet["scenario"])
        self.assertIsNone(packet["claim_guidance"])

    def test_parallel_upload_requires_lease(self):
        packet = json.loads(self.route("并行上传 T125"))
        self.assertEqual("shared-resource-lock", packet["scenario"])

    def test_upload_status_is_read_only(self):
        packet = json.loads(self.route("请汇报 T125 上传状态"))
        self.assertEqual("status-report", packet["scenario"])
        self.assertFalse(packet["claim_guidance"]["claim_available"])

    def test_powershell_path_literal(self):
        self.assertEqual("'K:/shared project/owner''s folder'", cli.powershell_literal("K:/shared project/owner's folder"))

    def test_review_only_forbids_execution(self):
        packet = json.loads(self.route("帮我审查 qoder 的 T125 方案，不要执行"))

        self.assertEqual("review-only", packet["scenario"])
        self.assertEqual("qoder", packet["target_agent"])
        self.assertIn("perform external writes", packet["forbidden_actions"])
        self.assertIn("claim the implementation task", packet["forbidden_actions"])
        self.assertFalse(packet["claim_guidance"]["claim_available"])

    def test_shared_external_write_requires_lease_and_operation(self):
        packet = json.loads(self.route("你们一起完成 T125 上传提交"))

        self.assertEqual("shared-resource-lock", packet["scenario"])
        self.assertIn("acquire lease", packet["allowed_actions"])
        self.assertTrue(any("kernel-lease-acquire" in command for command in packet["first_commands"]))
        self.assertTrue(any("kernel-op-reserve" in command for command in packet["first_commands"]))

    def test_takeover_stalled_task(self):
        packet = json.loads(self.route("WorkBuddy 卡住了，请接手 T125"))

        self.assertEqual("takeover", packet["scenario"])
        self.assertEqual("workbuddy", packet["target_agent"])
        self.assertIn("repeat unknown external operations", packet["forbidden_actions"])

    def test_wake_agent(self):
        packet = json.loads(self.route("通知 qoder 看一下 T114"))

        self.assertEqual("wake-agent", packet["scenario"])
        self.assertEqual("qoder", packet["target_agent"])
        self.assertEqual("T114", packet["task"])
        self.assertTrue(any("coord-wake" in command for command in packet["first_commands"]))

    def test_cli_less_fallback_markdown(self):
        text = self.route("这个 agent 不支持 CLI，只能读文件", markdown=True)

        self.assertIn("cli-less-fallback", text)
        self.assertIn("AEP Route", text)
        self.assertIn("read AEP_JOIN.md", text)


if __name__ == "__main__":
    unittest.main()
