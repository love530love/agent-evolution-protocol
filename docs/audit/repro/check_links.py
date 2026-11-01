"""Check that relative markdown links in a file resolve to existing paths.

Usage:  python docs/audit/repro/check_links.py README.md
Read-only. Writes nothing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def main(argv: list[str]) -> int:
    target = Path(argv[1] if len(argv) > 1 else "README.md")
    base = target.parent
    text = target.read_text(encoding="utf-8")
    missing: list[tuple[int, str, str]] = []
    total = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        for label, url in LINK_RE.findall(line):
            if url.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_part = url.split("#", 1)[0].strip()
            if not path_part:
                continue
            total += 1
            candidate = (base / path_part).resolve()
            if not candidate.exists():
                missing.append((lineno, label, url))
    print(f"file={target} relative_links={total} missing={len(missing)}")
    for lineno, label, url in missing:
        print(f"  L{lineno}: [{label}]({url})  -> NOT FOUND")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
