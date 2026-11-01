"""R-12: ``claim()`` is gate-then-mkdir, not one atomic step.  Two agents that
claim overlapping scopes at the same instant are both told ``claimed: true`` for
the same path, because the gate that is supposed to refuse the second one runs
before either has created its claim directory.

The docstring says: "it refuses to create a claim whose scope already belongs to
a live lease ... It never takes over an existing task ID and never overwrites
another owner."  Under concurrency it does both.

Run from the repository root:
  CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.tools/agent-runtime/Scripts/python.exe \
      docs/audit/repro/r12_claim_race.py
"""
import json
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PATH = "src/shared.py"


def attempt(args):
    coord, index, start = args
    from agent_evolution_protocol.coordination import claim_conflicts
    while time.monotonic() < start:      # line the processes up on the same instant
        time.sleep(0.0002)
    result = claim_conflicts.claim(coord, agent=f"agent{index}", task=f"task{index}",
                                   paths=[PATH])
    return index, bool(result.get("claimed")), result.get("reason", "")


def run(coord, workers, lead=1.0):
    start = time.monotonic() + lead
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(attempt, [(coord, index, start) for index in range(workers)]))


if __name__ == "__main__":
    from agent_evolution_protocol.coordination import claim_conflicts

    both = 0
    for trial in range(10):
        coord = tempfile.mkdtemp(prefix="aep-r12-two-")
        rows = run(coord, 2)
        won = sum(1 for row in rows if row[1])
        both += won == 2
        print(f"trial {trial}: 2 processes -> claimed {won}/2  {rows}")
    print(f"two processes both won {both}/10 trials\n")

    coord = tempfile.mkdtemp(prefix="aep-r12-eight-")
    rows = run(coord, 8, lead=3.0)
    print("8 processes      =", rows)
    print("claimed: true    =", sum(1 for row in rows if row[1]), "of 8")
    print("owners on disk   =", sorted(
        json.loads(path.read_text(encoding="utf-8"))["agent"]
        for path in (Path(coord) / "claims").glob("*/owner.json")))
    report = claim_conflicts.analyze(coord)
    print("analyze.status   =", report["status"], "blocking =", report["blocking"])
    print("coord =", coord)
