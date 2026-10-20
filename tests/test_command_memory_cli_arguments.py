import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


class CommandMemoryArgumentsTests(unittest.TestCase):
    def test_options_after_action_use_requested_root(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "isolated-coord"
            repo = Path(__file__).resolve().parents[1]
            run = subprocess.run([sys.executable, "-m", "agent_evolution_protocol.coordination.command_memory",
                "record", "--root", str(root), "--agent", "codex", "--command", "example",
                "--output", "observed error", "--exit-code", "1", "--source", "test-fixture"],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
                env={**os.environ, "PYTHONPATH": str(repo / "src")})
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIsInstance(json.loads(run.stdout), dict)
            self.assertTrue(list(root.rglob("*.json")))

    def test_run_accepts_explicit_command_separator(self):
        with TemporaryDirectory() as folder:
            repo = Path(__file__).resolve().parents[1]
            run = subprocess.run([sys.executable, "-m", "agent_evolution_protocol.coordination.command_memory",
                "run", "--root", str(Path(folder) / "coord"), "--agent", "codex", "--",
                sys.executable, "-c", "print('fixture-success')"],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
                env={**os.environ, "PYTHONPATH": str(repo / "src")})
            self.assertEqual(run.returncode, 0, run.stderr)
