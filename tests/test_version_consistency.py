import re
import os
from pathlib import Path
import subprocess
import sys

from agent_evolution_protocol import __version__


ROOT = Path(__file__).parents[1]


def test_package_and_project_versions_are_identical():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"$', text, re.MULTILINE)
    assert match
    assert match.group(1) == __version__ == "0.6.31"


def test_module_version_smoke_is_available_without_a_subcommand():
    """Release automation needs a real module-entry smoke test, not metadata only."""
    environment = os.environ.copy()
    source_root = str(ROOT / "src")
    environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-m", "agent_evolution_protocol", "--version"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == f"agent-evolution-protocol {__version__}"
