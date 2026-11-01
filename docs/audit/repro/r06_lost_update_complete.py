"""R-06: ``engine.complete()`` writes back a whole state object it did not
re-read under the lock.  A concurrent writer's commit is silently erased from
``state.json`` while its event stays in ``events.jsonl`` -- and ``recover()``
reports the chain as valid, so a restarted supervisor rebuilds "still running"
for a task that the log says was completed, and re-dispatches it.

``engine.update()`` exists for exactly this and says so in its docstring;
``complete`` / ``add_task`` / ``pause`` / ``resume`` do not use it.

Run from the repository root:
  CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.tools/agent-runtime/Scripts/python.exe \
      docs/audit/repro/r06_lost_update_complete.py
"""
import json
import tempfile
from pathlib import Path

from agent_evolution_protocol.federation import engine

root = Path(tempfile.mkdtemp(prefix="aep-r06-"))
state = engine.create(root, goal="demo", acceptance=["it runs"])
wid = state["workspace_id"]
engine.add_task(root, state, task_id="execute", requirement="run the README")
engine.add_task(root, state, task_id="review", requirement="review", deps=["execute"])

# Two processes, two independent reads -- the normal case for a scheduler and a
# supervisor running side by side.
a = engine.load(root, wid)
b = engine.load(root, wid)

a["tasks"]["execute"].update({"state": "running", "owner": "alpha", "attempts": 1})
engine.save(root, a)
engine.complete(root, a, task_id="execute")           # process A: work is done

engine.add_task(root, b, task_id="publish", requirement="publish")  # process B

disk = engine.load(root, wid)
events = [row["kind"] for row in engine.EventLog(root, wid).events()]
print("state.json execute.state =", disk["tasks"]["execute"]["state"])
print("state.json has publish   =", "publish" in disk["tasks"])
print("event kinds              =", events)
print("recover() says           =",
      {k: v for k, v in engine.recover(root, wid).items() if k != "state"})

# What a restarted supervisor does with that state:
print("ready_tasks()            =", engine.ready_tasks(disk))
print("root =", root)
