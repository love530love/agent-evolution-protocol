"""R-04: a claim directory left without ``owner.json`` (crash between mkdir and
the rename) makes three components disagree about the same path:

  gate()                 -> ALLOWED        (the path is unprotected)
  claim()                -> task_already_claimed, owner=None
  supervisor._claim_scopes -> skip         (the folder exists, so it is "claimed")

Net effect: the path is leased by nobody, the task can never take the lease,
and the supervisor records nothing at all -- so the workspace believes the scope
is covered while any other agent can take it.

Run from the repository root:
  CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.tools/agent-runtime/Scripts/python.exe \
      docs/audit/repro/r04_claim_orphan_dir.py
"""
import json
import tempfile
from pathlib import Path

from agent_evolution_protocol.coordination import claim_conflicts
from agent_evolution_protocol.federation import supervisor

coord = Path(tempfile.mkdtemp(prefix="aep-r04-"))

# 1. A healthy claim by another agent on a different path, to prove the tool works.
print("healthy claim:",
      json.dumps(claim_conflicts.claim(coord, agent="beta", task="other",
                                       paths=["docs/other.md"])["claimed"]))

# 2. The crash: claim() does mkdir(parents=True, exist_ok=False) and only then
#    writes owner.json.partial -> replace(owner.json).  A kill in between leaves
#    the folder with no owner.json.
(coord / "claims" / "execute").mkdir(parents=True, exist_ok=False)

# 3. What the gate says about that path for the task that owns the empty folder.
gate = claim_conflicts.gate(coord, agent="alpha", task="execute", paths=["src/app.py"])
print("gate.status            =", gate["status"], "allowed =", gate["allowed"])

# 4. What claim() says when the owner tries to take it.
result = claim_conflicts.claim(coord, agent="alpha", task="execute", paths=["src/app.py"])
print("claim.claimed          =", result["claimed"])
print("claim.reason           =", result["reason"])
print("claim.owner            =", result["owner"])

# 5. What the analysis says -- the orphan is invisible to it, too.
report = claim_conflicts.analyze(coord)
print("analyze.status         =", report["status"], "claims =", report["claims"])

# 6. What the supervisor does: skips silently, because the directory exists.
state = {"workspace_id": "W-" + "0" * 24,
         "tasks": {"execute": {"id": "execute", "state": "running", "owner": "alpha",
                               "paths": ["src/app.py"], "attempts": 1}},
         "agents": {}}
print("_claim_scopes          =", supervisor._claim_scopes(coord, state, coord))
print("task.state after       =", state["tasks"]["execute"]["state"])

# 7. And another agent takes the very path the first task believes it holds.
other = claim_conflicts.claim(coord, agent="gamma", task="sibling", paths=["src/app.py"])
print("sibling claim.claimed  =", other["claimed"], "reason =", other.get("reason"))
print("coord =", coord)
