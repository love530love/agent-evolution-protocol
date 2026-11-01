"""Compare CHANGELOG headings with docs/releases/*.md and pyproject version.

Usage: python docs/audit/repro/compare_versions.py
Read-only.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    in_changelog = re.findall(r"^##\s+(\d+\.\d+\.\d+)", changelog, re.M)
    order = re.findall(r"^##\s+(\d+\.\d+\.\d+)", changelog, re.M)
    in_releases = sorted(p.stem.lstrip("v") for p in (ROOT / "docs" / "releases").glob("v*.md"))
    pyproject = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)

    cl = set(in_changelog)
    rl = set(in_releases)
    print("pyproject version      :", pyproject.group(1) if pyproject else "?")
    print("changelog entries      :", len(in_changelog))
    print("release note files     :", len(in_releases))
    print("in CHANGELOG only      :", sorted(cl - rl))
    print("release note only      :", sorted(rl - cl))
    print("both                   :", len(cl & rl))
    def key(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split("."))
    expected = sorted(cl, key=key, reverse=True)
    print("changelog order correct:", order == expected)
    if order != expected:
        print("  actual order :", order)
        print("  expected     :", expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
