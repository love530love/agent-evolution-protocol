"""Positive controls: the things I tried that the code actually refuses.

Kept as a script because a red-team report that lists only failures is not a
measurement.  Every line here is an assertion about behaviour that held.

Run from the repository root:
  CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.tools/agent-runtime/Scripts/python.exe \
      docs/audit/repro/r14_positive_controls.py
"""
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from agent_evolution_protocol.coordination import claim_conflicts, result_readback
from agent_evolution_protocol.federation import engine


def locked_add(args):
    """Add ten tasks through engine.update(): re-read inside the lock."""
    root, wid, index = args
    from agent_evolution_protocol.federation import engine as e

    def change(state, index=index):
        for step in range(10):
            task_id = f"u{index}-{step}"
            state["tasks"][task_id] = {"id": task_id, "requirement": "x", "deps": [],
                                       "state": "pending", "owner": "", "attempts": 0,
                                       "signal": "", "note": "", "capability": "",
                                       "preferred": ""}

    for _ in range(1):
        e.update(root, wid, change)
    return index


if __name__ == "__main__":
    root = Path(tempfile.mkdtemp(prefix="aep-r14-"))
    state = engine.create(root, goal="demo", acceptance=["it runs"])
    wid = state["workspace_id"]

    for label, task_id, kwargs in (
        ("dep on a task that does not exist", "probe1", {"deps": ["ghost"]}),
        ("task that depends on itself", "probe2", {"deps": ["probe2"]}),
        ("duplicate task id", "probe3", {"deps": []}),
        ("duplicate task id (again)", "probe3", {"deps": []}),
    ):
        try:
            engine.add_task(root, state, task_id=task_id, requirement="x", **kwargs)
            print(f"{label}: ACCEPTED")
        except ValueError as exc:
            print(f"{label}: refused -> {exc}")

    # engine.update() is the documented concurrent-safe path: no lost writes.
    with ProcessPoolExecutor(max_workers=4) as pool:
        list(pool.map(locked_add, [(str(root), wid, index) for index in range(4)]))
    disk = engine.load(root, wid)
    print("update() with 4 processes: tasks in state =",
          len([k for k in disk["tasks"] if k.startswith("u")]), "(expected 40)")

    # Path scopes: escapes are refused, not rewritten.
    for bad in ("../etc/passwd", "/abs/path", "C:\\Windows\\x", "src/../../x", ""):
        try:
            claim_conflicts.normalize(bad)
            print(f"normalize({bad!r}): ACCEPTED (bad)")
        except ValueError as exc:
            print(f"normalize({bad!r}): refused -> {exc}")

    # Readback: an empty transcript is not agreement.
    claim = result_readback.claim({"task": "t", "phase": "done", "evidence": ["docs/a.md"]})
    verdict = result_readback.judge(claim, result_readback.observe([]))
    print("empty transcript -> match =", verdict["match"], "may_complete =",
          result_readback.may_complete(verdict))
    # A claim of "done" with no path is not a pass.
    verdict = result_readback.judge(result_readback.claim({"task": "t", "phase": "done"}),
                                    result_readback.observe([{"role": "assistant",
                                                              "direction": "incoming",
                                                              "added_lines": ["done!"]}]))
    print("no artifact      -> match =", verdict["match"], "reason =", verdict["reason"])
    print("root =", root)
