from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_evolution_protocol.coordination import commander, desktop_providers as providers


class DesktopProviderTests(unittest.TestCase):
    def test_catalog_includes_cua_driver_candidate(self):
        self.assertEqual(providers.CATALOG["cua_driver"]["project"], "trycua/cua")
        self.assertEqual(providers.CATALOG["cua_driver"]["priority"], 5)

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = {"name": "sbroenne", "command": sys.executable, "args": [],
                        "sdk_python": sys.executable, "bindings": {"locate": "ui_find"}}

    def discover(self):
        providers.register(self.root, self.profile)
        result = SimpleNamespace(returncode=0, stdout=json.dumps({"tools": [
            {"name": "ui_find", "inputSchema": {"type": "object"}}]}), stderr="")
        with patch.object(providers.subprocess, "run", return_value=result) as run:
            receipt = providers.probe(self.root, "sbroenne")
        self.assertNotIn("shell", run.call_args.kwargs)
        return receipt

    def test_export_is_explicit_and_does_not_leak_environment(self):
        providers.register(self.root, self.profile)
        config = providers.export_config(self.root, "sbroenne")
        self.assertEqual(config["mcpServers"]["sbroenne"]["command"], sys.executable)
        self.assertNotIn("sdk_python", str(config))
        for extra in ({"env": {"TOKEN": "secret"}}, {"cwd": str(self.root)}):
            providers.register(self.root, {**self.profile, **extra})
            with self.assertRaises(ValueError):
                providers.export_config(self.root, "sbroenne")

    def test_discovery_proves_tools_only_and_plan_needs_exact_binding(self):
        receipt = self.discover()
        self.assertEqual(receipt["status"], "TOOLS_DISCOVERED")
        self.assertFalse(receipt["desktop_input_performed"])
        self.assertFalse(receipt["delivery_verified"])
        self.assertEqual(providers.plan(self.root, operation="locate")["candidates"][0]["tool"], "ui_find")
        self.assertEqual(providers.plan(self.root, operation="type")["candidates"], [])

    def test_missing_autoit_blocks_before_starting_a_server(self):
        profile = {**self.profile, "name": "mario", "required_paths": [str(self.root / "AutoIt3.exe")]}
        providers.register(self.root, profile)
        with patch.object(providers.subprocess, "run") as run, self.assertRaisesRegex(ValueError, "missing dependencies"):
            providers.probe(self.root, "mario")
        run.assert_not_called()
        row = next(r for r in providers.inventory(self.root)["providers"] if r["name"] == "mario")
        self.assertEqual(row["status"], "DEPENDENCY_MISSING")

    def test_configuration_change_and_expiry_invalidate_plans(self):
        self.discover()
        providers.register(self.root, {**self.profile, "args": ["changed"]})
        self.assertEqual(providers.plan(self.root, operation="locate")["candidates"], [])
        self.discover()
        path = self.root / "desktop_providers" / "receipts" / "sbroenne.json"
        row = json.loads(path.read_text(encoding="utf-8"))
        row["at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        path.write_text(json.dumps(row), encoding="utf-8")
        self.assertEqual(providers.plan(self.root, operation="locate")["candidates"], [])

    def test_uncertain_send_has_no_automatic_failover(self):
        self.discover()
        order = commander.submit_order(self.root, target="qoder", task="notice", text="read inbox", sender="codex")
        deferred = commander._record_attempt(self.root, order, state="DEFERRED")
        self.assertEqual(deferred["attempts"][-1]["receipt"]["desktop_provider_plan"]["status"], "PLAN_ONLY")
        ceased = commander._record_attempt(self.root, order, state="CEASED")
        plan = ceased["attempts"][-1]["receipt"]["desktop_provider_plan"]
        self.assertEqual(plan["status"], "VERIFY_PREVIOUS_ATTEMPT")
        self.assertFalse(plan["execution_started"])
        self.assertEqual(plan["candidates"], [])

    def test_probe_timeout_is_recorded_without_delivery_claim(self):
        providers.register(self.root, self.profile)
        with patch.object(providers.subprocess, "run", side_effect=subprocess.TimeoutExpired("sdk", 40)):
            result = providers.probe(self.root, "sbroenne")
        self.assertEqual(result["status"], "PROBE_FAILED")
        self.assertFalse(result["delivery_verified"])

    def test_registration_rejects_shell_scripts_and_path_traversal(self):
        for update in ({"name": "../bad"}, {"args": "shell string"}, {"bindings": {"execute_anything": "x"}}, {"priority": True}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                providers.register(self.root, {**self.profile, **update})

    def test_runtime_failure_preserves_server_root_cause(self):
        providers.register(self.root, {**self.profile, "env": {"ANONYMIZED_TELEMETRY": "false"}})
        failure = SimpleNamespace(returncode=2, stdout=json.dumps({
            "error": "Connection closed", "server_stderr": "Cannot find module key_sender.node"}), stderr="")
        with patch.object(providers.subprocess, "run", return_value=failure):
            result = providers.probe(self.root, "sbroenne")
        self.assertEqual(result["status"], "PROBE_FAILED")
        self.assertIn("key_sender.node", result["server_stderr"])
        self.assertEqual(providers.plan(self.root, operation="locate")["candidates"], [])
        with self.assertRaisesRegex(ValueError, "env"):
            providers.register(self.root, {**self.profile, "env": {"A": None}})

    def _agent_desktop_profile(self):
        return {"name": "agent_desktop", "command": sys.executable, "args": [],
                "sdk_python": sys.executable, "bindings": {"snapshot": "desktop_snapshot"}}

    @staticmethod
    def _unique_window(*, anchored=True):
        return {"clients": [{"agent": "qoder", "out_of_scope": "", "windows": [{
            "pid": 42, "title": "Qoder", "identity": "exe", "anchor_status": "CURRENT",
            "is_anchor": anchored,
        }]}]}

    def test_capability_probe_separates_usable_tree_from_session_acceptance(self):
        providers.register(self.root, self._agent_desktop_profile())
        responses = [
            {"ok": True, "data": [{"id": "w-42", "pid": 42, "title": "Qoder",
                                  "process_instance": "instance-42", "accessible": True}]},
            {"ok": True, "data": {"ref_count": 3, "snapshot_id": "normal", "window": {"id": "w-42"},
                                  "tree": {"secret": "must not persist", "children": [
                                      {"ref_id": "@r1", "role": "textbox", "available_actions": ["SetValue"]}
                                  ]}}},
            {"ok": True, "data": {"ref_count": 4, "snapshot_id": "forced", "window": {"id": "w-42"},
                                  "tree": {"children": [
                                      {"ref_id": "@r2", "role": "edit", "available_actions": ["SetValue"]}
                                  ]}}},
        ]
        with patch.object(providers.client_registry, "discover", return_value=self._unique_window()), \
             patch.object(providers.client_registry, "profile", return_value={"electron": True}), \
             patch.object(providers, "_run_json", side_effect=responses) as runner:
            result = providers.capability_probe(self.root, "qoder", timeout=3)
        self.assertEqual(result["status"], "SESSION_UNATTESTED")
        self.assertEqual(result["tree_status"], "TREE_USABLE")
        self.assertFalse(result["delivery_verified"])
        self.assertFalse(result["session_accepted"])
        self.assertEqual(len(result["routes"]), 2)
        self.assertTrue(all("tree" not in route for route in result["routes"]))
        self.assertEqual(runner.call_count, 3)
        receipt = json.loads((self.root / "desktop_providers" / "capabilities" /
                              "agent_desktop--qoder.json").read_text(encoding="utf-8"))
        self.assertNotIn("secret", json.dumps(receipt))

    def test_capability_probe_records_opaque_tree_without_attempting_input(self):
        providers.register(self.root, self._agent_desktop_profile())
        responses = [
            {"ok": True, "data": [{"id": "w-42", "pid": 42, "title": "Qoder"}]},
            {"ok": True, "data": {"ref_count": 0, "snapshot_id": "opaque", "window": {"id": "w-42"}}},
        ]
        with patch.object(providers.client_registry, "discover", return_value=self._unique_window(anchored=False)), \
             patch.object(providers.client_registry, "profile", return_value={"electron": False}), \
             patch.object(providers, "_run_json", side_effect=responses):
            result = providers.capability_probe(self.root, "qoder", timeout=3)
        self.assertEqual(result["status"], "TREE_OPAQUE")
        self.assertEqual(result["window_resolution"]["anchor_status"], "CURRENT")
        self.assertFalse(result["desktop_input_performed"])
        self.assertFalse(result["message_visible"])
        self.assertFalse(result["agent_ack"])

    def test_capability_probe_marks_titlebar_only_tree_partial(self):
        providers.register(self.root, self._agent_desktop_profile())
        responses = [
            {"ok": True, "data": [{"id": "w-42", "pid": 42, "title": "Qoder"}]},
            {"ok": True, "data": {"ref_count": 1, "snapshot_id": "titlebar", "window": {"id": "w-42"},
                                  "tree": {"role": "dialog", "children": [{
                                      "ref_id": "@title", "role": "group", "available_actions": ["SetValue"],
                                      "native_id": {"kind": "automation_id", "value": "TitleBar"},
                                  }]}}},
        ]
        with patch.object(providers.client_registry, "discover", return_value=self._unique_window()), \
             patch.object(providers.client_registry, "profile", return_value={"electron": False}), \
             patch.object(providers, "_run_json", side_effect=responses):
            result = providers.capability_probe(self.root, "qoder", timeout=3)
        self.assertEqual(result["status"], "TREE_PARTIAL")
        self.assertEqual(result["routes"][0]["composer_input_count"], 0)

    def test_capability_probe_refuses_ambiguous_or_out_of_scope_targets_before_provider_io(self):
        providers.register(self.root, self._agent_desktop_profile())
        ambiguous = {"clients": [{"agent": "qoder", "out_of_scope": "", "windows": [{"pid": 1}, {"pid": 2}]}]}
        outside = {"clients": [{"agent": "chrome", "out_of_scope": "human chat", "windows": []}]}
        with patch.object(providers.client_registry, "discover", return_value=ambiguous), \
             patch.object(providers, "_run_json") as runner:
            result = providers.capability_probe(self.root, "qoder", timeout=3)
        self.assertEqual(result["status"], "WINDOW_AMBIGUOUS")
        runner.assert_not_called()
        with patch.object(providers.client_registry, "discover", return_value=outside), \
             patch.object(providers, "_run_json") as runner:
            result = providers.capability_probe(self.root, "chrome", timeout=3)
        self.assertEqual(result["status"], "OUT_OF_SCOPE")
        runner.assert_not_called()

    def test_capability_probe_classifies_provider_timeout_as_tree_timeout(self):
        providers.register(self.root, self._agent_desktop_profile())
        responses = [
            {"ok": True, "data": [{"id": "w-42", "pid": 42, "title": "Qoder"}]},
            {"ok": False, "error": {"code": "TIMEOUT", "message": "snapshot timed out"}},
        ]
        with patch.object(providers.client_registry, "discover", return_value=self._unique_window()), \
             patch.object(providers.client_registry, "profile", return_value={"electron": False}), \
             patch.object(providers, "_run_json", side_effect=responses):
            result = providers.capability_probe(self.root, "qoder", timeout=3)
        self.assertEqual(result["status"], "TREE_TIMEOUT")
        self.assertEqual(result["routes"][0]["status"], "TREE_TIMEOUT")
