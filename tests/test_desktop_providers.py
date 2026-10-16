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
