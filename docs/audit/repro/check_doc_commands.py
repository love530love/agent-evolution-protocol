"""Extract documented CLI subcommands from docs and check they exist in argparse.

Usage: python docs/audit/repro/check_doc_commands.py
Read-only: imports the CLI parser and compares its choice set with tokens
mentioned in README.md and docs/*.md.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from agent_evolution_protocol import cli  # noqa: E402

TOKEN = re.compile(r"\b(coord-[a-z0-9-]+|kernel-[a-z0-9-]+)\b")


def known_subcommands() -> set[str]:
    import argparse
    parser = cli.build_parser() if hasattr(cli, "build_parser") else None
    if parser is None:
        for name in dir(cli):
            obj = getattr(cli, name)
            if isinstance(obj, argparse.ArgumentParser):
                parser = obj
                break
    found: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            found |= set(action.choices)
    return found


def main() -> int:
    known = known_subcommands()
    print(f"parser subcommands: {len(known)}")
    docs = [ROOT / "README.md", ROOT / "README.zh-CN.md", *sorted((ROOT / "docs").rglob("*.md"))]
    missing: dict[str, list[str]] = {}
    for path in docs:
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in set(TOKEN.findall(text)):
            if token not in known:
                missing.setdefault(token, []).append(str(path.relative_to(ROOT)))
    print(f"unknown coord-/kernel- tokens in docs: {len(missing)}")
    for token, where in sorted(missing.items()):
        print(f"  {token:34s} x{len(where):<3} e.g. {where[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
