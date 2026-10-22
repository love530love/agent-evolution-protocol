import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_evolution_protocol.coordination import orca_adapter as adapter


class OrcaAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        adapter.configure(self.root, sys.executable)
        self.identity = dict(handle="terminal:one", executionHostId="local-host", ptyId="pty-1",
            incarnationId="incarnation-1", worktreeId="wt-1", worktreePath="/fixture", agentIdentity="codex")
        self.row = {**self.identity, "verdict": "live", "writable": True}
        self.runtime = {"runtimeId": "runtime-1", "reachable": True, "capabilities": ["terminal.prompt-delivery.v1"]}
        self.calls = []

    def call(self, root, arguments, timeout=15):
        self.calls.append(arguments)
        if arguments == ["status"]:
            return "runtime-1", {"runtime": self.runtime}
        if arguments == ["terminal", "list"]:
            return "runtime-1", {"terminals": [self.row], "truncated": False, "hostScope": {"hostIds": ["local-host"]}}
        if arguments[:2] == ["terminal", "read"]:
            return "runtime-1", {"terminal": {"handle": "terminal:one", "tail": ["echo AEP-ACK:fixture"], "nextCursor": "10"}}
        if arguments[:2] == ["terminal", "send"]:
            return "runtime-1", {"send": {"handle": "terminal:one", "accepted": True,
                "prompt": {"requestId": "prompt-1", "observation": "supported", "processIncarnation": "incarnation-1", "stages": ["input_accepted"]}}}
        raise AssertionError(arguments)

    def bind(self):
        adapter.bind(self.root, agent="codex", terminal="terminal:one")

    def test_configuration_cannot_be_silently_replaced(self):
        original = (self.root / "orca" / "config.json").read_bytes()
        with self.assertRaises(ValueError):
            adapter.configure(self.root, sys.executable)
        self.assertEqual((self.root / "orca" / "config.json").read_bytes(), original)

    def test_doctor_is_read_only_and_does_not_infer_ack(self):
        with patch.object(adapter, "_call", side_effect=self.call):
            before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
            self.assertEqual(adapter.doctor(self.root)["status"], "BINDABLE")
            self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*")))
            self.bind()
            report = adapter.doctor(self.root)
            self.assertEqual(report["status"], "BOUND_PROCESS_ONLY")
            self.assertFalse(report["agent_ack_verified"])
            self.row["incarnationId"] = "replacement"
            self.assertEqual(adapter.doctor(self.root)["status"], "BLOCKED")
        self.assertTrue(all(args in (["status"], ["terminal", "list"]) for args in self.calls))

    def test_doctor_unconfigured_invalid_and_failed_probe(self):
        missing = self.root / "missing"
        with patch.object(adapter, "_call") as called:
            self.assertEqual(adapter.doctor(missing)["status"], "UNCONFIGURED")
            self.assertFalse(missing.exists())
            config = self.root / "orca" / "config.json"
            original = config.read_bytes()
            config.write_text("[]", encoding="utf-8")
            self.assertEqual(adapter.doctor(self.root)["status"], "INVALID_CONFIGURATION")
            called.assert_not_called()
            config.write_bytes(original)
        with patch.object(adapter, "_call", side_effect=subprocess.TimeoutExpired("fixture", 15)):
            self.assertEqual(adapter.doctor(self.root)["status"], "PROBE_FAILED")

    def test_doctor_missing_capability_and_malformed_inventory(self):
        with patch.object(adapter, "_call", side_effect=self.call):
            self.runtime["capabilities"] = []
            self.assertEqual(adapter.doctor(self.root)["status"], "BLOCKED")
            self.runtime["capabilities"] = "terminal.prompt-delivery.v1"
            self.assertEqual(adapter.doctor(self.root)["status"], "PROBE_FAILED")

    def test_explicit_target_dedup_and_no_ack_from_echo(self):
        with patch.object(adapter, "_call", side_effect=self.call):
            self.bind()
            read = adapter.read(self.root, agent="codex", cursor="0")
            self.assertFalse(read["role_attributed"])
            self.assertFalse(read["agent_ack_verified"])
            self.assertEqual(adapter.send(self.root, agent="codex", event_id="e1", text="hello")["status"], "PLAN_ONLY")
            sent = adapter.send(self.root, agent="codex", event_id="e1", text="hello", apply=True)
            self.assertEqual(sent["status"], "PROMPT_ACCEPTED")
            count = len(self.calls)
            self.assertTrue(adapter.send(self.root, agent="codex", event_id="e1", text="hello", apply=True)["replayed_without_sending"])
            self.assertEqual(len(self.calls), count)
            with self.assertRaises(ValueError):
                adapter.send(self.root, agent="codex", event_id="e1", text="changed", apply=True)
            self.assertFalse(sent["conversation_session_verified"])
            sends = [args for args in self.calls if args[:2] == ["terminal", "send"]]
            self.assertEqual(len(sends), 1)
            self.assertEqual(sends[0][2:4], ["--terminal", "terminal:one"])

    def test_replaced_process_and_unknown_host_block_before_input(self):
        with patch.object(adapter, "_call", side_effect=self.call):
            self.bind()
            self.row["incarnationId"] = "replaced"
            with self.assertRaises(ValueError):
                adapter.send(self.root, agent="codex", event_id="e2", text="hello", apply=True)
            self.assertFalse(any(args[:2] == ["terminal", "send"] for args in self.calls))
            self.row = {**self.identity, "verdict": "unverifiable", "writable": True}
            with self.assertRaises(ValueError):
                adapter.read(self.root, agent="codex")

    def test_timeout_persists_unknown_and_is_not_retried(self):
        with patch.object(adapter, "_call", side_effect=self.call):
            self.bind()
        def timeout(root, arguments, timeout=15):
            if arguments[:2] == ["terminal", "send"]:
                raise subprocess.TimeoutExpired("fixture-cli", 20)
            return self.call(root, arguments)
        with patch.object(adapter, "_call", side_effect=timeout):
            result = adapter.send(self.root, agent="codex", event_id="e3", text="hello", apply=True)
        self.assertEqual(result["status"], "OUTCOME_UNKNOWN")
        with patch.object(adapter, "_call") as called:
            replay = adapter.send(self.root, agent="codex", event_id="e3", text="hello", apply=True)
            called.assert_not_called()
        self.assertEqual(replay["status"], "OUTCOME_UNKNOWN")

    def test_missing_capability_shell_identity_and_control_codes_rejected(self):
        with patch.object(adapter, "_call", side_effect=self.call):
            self.bind()
            self.runtime["capabilities"] = []
            with self.assertRaises(ValueError):
                adapter.send(self.root, agent="codex", event_id="e4", text="hello", apply=True)
            self.row["agentIdentity"] = "shell"
            with self.assertRaises(ValueError):
                adapter.bind(self.root, agent="other", terminal="terminal:one")
        with self.assertRaises(ValueError):
            adapter.send(self.root, agent="codex", event_id="e4", text="\x1b[31m", apply=True)

    def test_interrupted_sender_keeps_reservation_and_cannot_blind_retry(self):
        with patch.object(adapter, "_call", side_effect=self.call):
            self.bind()
        def interrupted(root, arguments, timeout=15):
            if arguments[:2] == ["terminal", "send"]:
                raise RuntimeError("simulated sender interruption after reservation")
            return self.call(root, arguments)
        with patch.object(adapter, "_call", side_effect=interrupted), self.assertRaises(RuntimeError):
            adapter.send(self.root, agent="codex", event_id="e5", text="hello", apply=True)
        with patch.object(adapter, "_call") as called:
            result = adapter.send(self.root, agent="codex", event_id="e5", text="hello", apply=True)
            called.assert_not_called()
        self.assertEqual(result["status"], "RESERVED")

    def test_cli_envelope_and_no_shell_invocation(self):
        envelope = {"ok": True, "_meta": {"runtimeId": "runtime-1"}, "result": {"runtime": self.runtime}}
        run = SimpleNamespace(returncode=0, stdout=json.dumps(envelope))
        with patch.object(adapter.subprocess, "run", return_value=run) as called:
            adapter._call(self.root, ["status"])
        self.assertNotIn("shell", called.call_args.kwargs)
        self.assertEqual(called.call_args.args[0][-3:], ["--host", "local", "--json"])
        run.stdout = json.dumps({"ok": True, "result": {}, "_meta": "invalid"})
        with patch.object(adapter.subprocess, "run", return_value=run), self.assertRaises(ValueError):
            adapter._call(self.root, ["status"])

    def test_wait_is_bounded_and_never_completion(self):
        with patch.object(adapter, "_call", side_effect=self.call):
            self.bind()
        def waited(root, arguments, timeout=15):
            if arguments[:2] == ["terminal", "wait"]:
                self.assertEqual(arguments[-1], "1000")
                return "runtime-1", {"wait": {"satisfied": True}}
            return self.call(root, arguments)
        with patch.object(adapter, "_call", side_effect=waited):
            result = adapter.wait(self.root, agent="codex", seconds=1)
        self.assertFalse(result["task_completed"])
        with self.assertRaises(ValueError):
            adapter.wait(self.root, agent="codex", seconds=31)
