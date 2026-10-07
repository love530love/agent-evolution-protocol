import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_evolution_protocol import cli


class WorkspaceJoinTest(unittest.TestCase):
    def test_workspace_init_writes_natural_language_join_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            cli.workspace_init(SimpleNamespace(
                workspace=str(workspace),
                coord_dir=None,
                kernel_dir=None,
                default_task="T125",
                agents_md=True,
            ))

            config_path = workspace / ".aep" / "workspace.json"
            join_path = workspace / "AEP_JOIN.md"
            agents_path = workspace / "AGENTS.md"

            self.assertTrue(config_path.exists())
            self.assertTrue(join_path.exists())
            self.assertTrue(agents_path.exists())

            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(str(workspace), config["workspace"])
            self.assertEqual("T125", config["default_task"])
            self.assertIn("请加入这个工作区协作", join_path.read_text(encoding="utf-8"))
            self.assertIn("AEP_JOIN.md", agents_path.read_text(encoding="utf-8"))

    def test_discover_workspace_config_from_child_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            child = workspace / "src" / "pkg"
            child.mkdir(parents=True)
            cli.workspace_init(SimpleNamespace(
                workspace=str(workspace),
                coord_dir=None,
                kernel_dir=None,
                default_task="",
                agents_md=False,
            ))
            config = cli.discover_workspace_config(child)
            self.assertEqual(str(workspace), config["workspace"])
            self.assertTrue(config["coord_root"].endswith("coordination"))


if __name__ == "__main__":
    unittest.main()
