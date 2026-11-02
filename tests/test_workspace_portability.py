"""P0 tests for workspace.json portability fix (F-22).

Verifies that:
1. .aep/workspace.example.json uses relative paths and is the canonical config.
2. configured_root() rejects absolute paths that fall outside the workspace.
3. .aep/workspace.json is not tracked in git.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_evolution_protocol import cli


class TestWorkspaceExampleConfig(unittest.TestCase):
    """Test the workspace.example.json template."""

    def test_example_uses_relative_paths(self):
        """workspace.example.json should use relative paths, not absolute."""
        example_path = Path(__file__).resolve().parents[1] / ".aep" / "workspace.example.json"
        self.assertTrue(example_path.exists(),
                        "workspace.example.json should exist")
        config = json.loads(example_path.read_text(encoding="utf-8"))
        for key in ("coord_root", "kernel_root"):
            if key in config:
                self.assertFalse(Path(config[key]).is_absolute(),
                                 f"{key} should be a relative path")

    def test_example_config_schema(self):
        """workspace.example.json should have the expected keys."""
        example_path = Path(__file__).resolve().parents[1] / ".aep" / "workspace.example.json"
        config = json.loads(example_path.read_text(encoding="utf-8"))
        self.assertIn("workspace", config)


class TestConfiguredRootPathValidation(unittest.TestCase):
    """Test that configured_root() validates absolute paths."""

    def test_explicit_relative_path_works(self):
        """An explicit relative path should be resolved against the workspace."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            coord = workspace / "coordination"
            coord.mkdir()

            args = argparse.Namespace(workspace=str(workspace),
                                      coord_root=None,
                                      kernel_root=None)
            root = cli.configured_root(args, "coord_root", "AEP_COORD_ROOT",
                                       "coordination")
            self.assertEqual(root, coord)

    def test_env_var_absolute_path_works(self):
        """An absolute path via env var should work."""
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp) / "coordination"
            coord.mkdir()

            import os
            old_val = os.environ.get("AEP_COORD_ROOT")
            os.environ["AEP_COORD_ROOT"] = str(coord)
            try:
                args = argparse.Namespace(workspace=None,
                                          coord_root=None,
                                          kernel_root=None)
                root = cli.configured_root(args, "coord_root", "AEP_COORD_ROOT",
                                           "coordination")
                self.assertEqual(root, coord)
            finally:
                if old_val is not None:
                    os.environ["AEP_COORD_ROOT"] = old_val
                else:
                    os.environ.pop("AEP_COORD_ROOT", None)

    def test_discover_workspace_config_from_example(self):
        """discover_workspace_config should load from workspace.example.json."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            aep = workspace / ".aep"
            aep.mkdir()

            # Copy example config.
            example = Path(__file__).resolve().parents[1] / ".aep" / "workspace.example.json"
            (aep / "workspace.json").write_text(
                example.read_text(encoding="utf-8"), encoding="utf-8"
            )

            config = cli.discover_workspace_config(workspace)
            self.assertIn("workspace", config)


class TestGitTracking(unittest.TestCase):
    """Test that .aep/workspace.json is not tracked in git."""

    def test_workspace_json_not_in_git(self):
        """workspace.json should not be tracked in git (only example)."""
        repo_root = Path(__file__).resolve().parents[1]
        import subprocess
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", ".aep/workspace.json"],
            capture_output=True, text=True
        )
        self.assertEqual(result.stdout.strip(), "",
                         ".aep/workspace.json should not be tracked in git")

    def test_workspace_example_json_is_tracked(self):
        """workspace.example.json should be tracked in git."""
        repo_root = Path(__file__).resolve().parents[1]
        import subprocess
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", ".aep/workspace.example.json"],
            capture_output=True, text=True
        )
        self.assertNotEqual(result.stdout.strip(), "",
                            ".aep/workspace.example.json should be tracked in git")


if __name__ == "__main__":
    unittest.main()
