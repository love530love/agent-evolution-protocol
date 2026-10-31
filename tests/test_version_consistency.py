import re
from pathlib import Path

from agent_evolution_protocol import __version__


ROOT = Path(__file__).parents[1]


def test_package_and_project_versions_are_identical():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"$', text, re.MULTILINE)
    assert match
    assert match.group(1) == __version__ == "0.6.4"
