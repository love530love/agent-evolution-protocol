"""R-13: the same lost update as R-06, under real processes.  ``add_task`` and
``complete`` write back a state object they did not re-read under the lock, so
concurrent writers erase each other from ``state.json`` while every write leaves
its event in ``events.jsonl``.  ``recover()`` then reports the chain valid.

Contrast: ``engine.tick`` *does* re-read inside the lock and loses nothing --
that is the pattern the other writers do not use.

Run from the repository root:
  CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.tools/agent-runtime/Scripts/python.exe \
      docs/audit/repro/r13_concurrent_add_task.py
"""
import json
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path


def worker(args):
    root, wid, index, start = args
    from agent_evolution_protocol.federation import engine
    state = engine.load(root, wid)
    while time.monotonic() < start:
        time.sleep(0.0005)
    for step in range(10):
        engine.add_task(root, state, task_id=f"t{index}-{step}", requirement="x")
    return index


if __name__ == "__main__":
    from agent_evolution_protocol.federation import engine

    root = Path(tempfile.mkdtemp(prefix="aep-r13-"))
    state = engine.create(root, goal="demo", acceptance=["it runs"])
    wid = state["workspace_id"]
    start = time.monotonic() + 2.0
    with ProcessPoolExecutor(max_workers=4) as pool:
        list(pool.map(worker, [(str(root), wid, index, start) for index in range(4)]))

    disk = engine.load(root, wid)
    events = engine.EventLog(root, wid).events()
    added_events = [row["payload"]["task"] for row in events if row["kind"] == "task_added"]
    print("task_added events   =", len(added_events))
    print("tasks in state.json =", len(disk["tasks"]))
    print("missing from state  =", sorted(set(added_events) - set(disk["tasks"]))[:8])
    chain = engine.EventLog(root, wid).verify()
    print("chain.valid         =", chain["valid"], "events =", chain["events"])
    print("recover().tasks     =", engine.recover(root, wid)["tasks"])
    print("root =", root)
