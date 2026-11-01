"""R-10: ``assignment.assign`` reports the claim refusal in its receipt and then
deposits the order anyway, marking the task ``running`` with an owner.  The one
call that is documented as writing "existence + ownership + acceptance" keeps
going when ownership fails, so an agent is dispatched into a write scope another
agent already holds.

Run from the repository root:
  CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.tools/agent-runtime/Scripts/python.exe \
      docs/audit/repro/r10_assign_ignores_claim_refusal.py
"""
import json
import tempfile
from pathlib import Path

from agent_evolution_protocol.coordination import claim_conflicts, commander
from agent_evolution_protocol.federation import assignment, engine

base = Path(tempfile.mkdtemp(prefix="aep-r10-"))
root, coord = str(base / "state"), str(base / "coordination")
state = engine.create(root, goal="demo", acceptance=["it runs"])
wid = state["workspace_id"]

# Somebody else already owns the file.
held = claim_conflicts.claim(coord, agent="gamma", task="OTHER", paths=["src/app.py"])
print("incumbent claim.claimed =", held["claimed"])

receipt = assignment.assign(coord, workspace_id=wid, task_id="execute",
                            requirement="改 src/app.py", target="alpha",
                            paths=["src/app.py"], federation_root=root)
print("receipt.evidence       =", json.dumps(receipt["evidence"], ensure_ascii=False))
print("receipt.claim.claimed  =", receipt["claim"]["claimed"])
print("receipt.claim.reason   =", receipt["claim"].get("reason"))

after = engine.load(root, wid)
print("task.state             =", after["tasks"]["execute"]["state"])
print("task.owner             =", after["tasks"]["execute"]["owner"])
orders = commander._orders(coord)
print("orders deposited       =", len(orders), [(o["target"], o["task"], o["state"]) for o in orders])
print("who holds src/app.py   =",
      json.loads((Path(coord) / "claims" / "OTHER" / "owner.json").read_text())["agent"])
print("base =", base)
