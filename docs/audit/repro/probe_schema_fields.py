"""Run documented read-only CLI commands and report which emit a top-level "schema" key.

Usage: python docs/audit/repro/probe_schema_fields.py <workdir> <coord-root>
Read-only probe: only runs commands the docs describe as read-only/preview.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CMDS = [
    ["coord-status"],
    ["coord-digest", "--agent", "newhire"],
    ["coord-onboarding", "--agent", "newhire"],
    ["doctor"],
    ["coord-provider-catalog"],
    ["coord-delivery-audit", "--summary-only"],
    ["route", "--agent", "newhire", "--intent", "Join this task", "--markdown"],
    ["join", "--agent", "newhire", "--task", "AEP-ROBUSTNESS-20261028", "--markdown"],
    ["inspect", "--markdown"],
    ["kernel-status"],
]


def main(argv: list[str]) -> int:
    workdir = Path(argv[1])
    coord = argv[2]
    for cmd in CMDS:
        full = ["aep", "--coord-root", coord, *cmd]
        proc = subprocess.run(full, cwd=workdir, capture_output=True, text=True, timeout=180)
        out = proc.stdout.strip()
        schema = None
        try:
            parsed = json.loads(out)
            if isinstance(parsed, dict):
                schema = parsed.get("schema") or parsed.get("schema_version")
        except Exception:
            parsed = None
        kind = "json" if parsed is not None else ("markdown" if out.startswith("#") else "other")
        print(f"{' '.join(cmd):60s} rc={proc.returncode} kind={kind} schema={schema}")
        if proc.returncode != 0:
            print("   stderr:", proc.stderr.strip().splitlines()[:3])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
