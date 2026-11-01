"""Check which `schema` identifier strings emitted by src/ have a schema file.

Usage: python docs/audit/repro/check_schema_coverage.py
Read-only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LITERAL = re.compile(r'"schema"\s*:\s*"([A-Za-z0-9._-]+)"')


def main() -> int:
    declared = {}
    for path in sorted((ROOT / "schemas").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        declared[data["properties"]["schema"]["const"]] = path.name

    emitted: dict[str, set[str]] = {}
    for path in sorted((ROOT / "src").rglob("*.py")):
        for value in LITERAL.findall(path.read_text(encoding="utf-8", errors="replace")):
            emitted.setdefault(value, set()).add(str(path.relative_to(ROOT)))

    print(f"schema files: {len(declared)}")
    for const, name in sorted(declared.items()):
        print(f"  file {name:52s} const={const}")
    print(f"\ndistinct schema ids emitted by src: {len(emitted)}")
    covered = [v for v in emitted if v in declared]
    print(f"covered by a schema file: {len(covered)} -> {sorted(covered)}")
    print(f"NOT covered: {len(emitted) - len(covered)}")
    for value in sorted(emitted):
        if value not in declared:
            print(f"  {value:44s} {sorted(emitted[value])[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
