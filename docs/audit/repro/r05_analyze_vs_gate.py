"""R-05: two answers to one question.  ``analyze()`` reports a blocking
``invalid_scope`` conflict and ``write_allowed: false``; ``gate()`` -- the
function a writer actually calls before touching a file -- reports
``ALLOWED`` for the same directory at the same instant.

``gate()`` re-derives its own blocking list from ``analyze()`` and keeps only
``unreadable_claim`` / ``duplicate_task_id``, so ``invalid_scope`` stops being
blocking one function later.

Run from the repository root:
  CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.tools/agent-runtime/Scripts/python.exe \
      docs/audit/repro/r05_analyze_vs_gate.py
"""
import json
import tempfile
from pathlib import Path

from agent_evolution_protocol.coordination import claim_conflicts

coord = Path(tempfile.mkdtemp(prefix="aep-r05-"))

# A claim written by hand (legacy record, or an agent that ran the tool before
# normalization was added) whose scope escapes the repository.
folder = coord / "claims" / "legacy"
folder.mkdir(parents=True)
(folder / "owner.json").write_text(json.dumps({
    "task": "legacy", "agent": "beta", "at": "2026-11-01T00:00:00+00:00",
    "paths": ["../outside/secret.txt"],
}), encoding="utf-8")

report = claim_conflicts.analyze(coord)
print("analyze.status       =", report["status"])
print("analyze.write_allowed=", report["write_allowed"])
print("analyze.blocking     =", report["blocking"])
print("analyze.conflicts    =", json.dumps(
    [{k: c[k] for k in ("kind", "severity", "path")} for c in report["conflicts"]],
    ensure_ascii=False))

gate = claim_conflicts.gate(coord, agent="alpha", task="execute", paths=["docs/shared.md"])
print("gate.status          =", gate["status"])
print("gate.allowed         =", gate["allowed"])
print("gate.blocking        =", json.dumps(gate["blocking"], ensure_ascii=False))

# The same question through the CLI, which is what a writer runs:
import subprocess
import sys
print("cli analyze exit     =", subprocess.run(
    [sys.executable, "-m", "agent_evolution_protocol.coordination.claim_conflicts",
     "analyze", str(coord)], capture_output=True, text=True).returncode)
print("cli gate exit        =", subprocess.run(
    [sys.executable, "-m", "agent_evolution_protocol.coordination.claim_conflicts",
     "gate", str(coord), "--agent", "alpha", "--task", "execute", "docs/shared.md"],
    capture_output=True, text=True).returncode, "(0 == ALLOWED, 3 == BLOCKED)")
print("coord =", coord)
