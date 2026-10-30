"""Deterministic comparison of solo work against coordinated work.

The point of this module is to make the word "better" falsifiable. It runs the
same task set twice — once as one unattended worker, once through claim gates,
reinforcement and quorum — and reports both outcomes with their denominators,
including the costs that coordination adds.

This is a **model**, not a measurement of a real deployment. It contains no
wall-clock time, no randomness and no I/O: ``steps`` is a unit-cost proxy, so
two runs of the same input always produce the same report. Real numbers must
come from actual runs; see ``limitations`` in every report.
"""
from __future__ import annotations

from .claim_conflicts import overlaps, normalize


LIMITATIONS = (
    "simulation only: no real processes, network, or agent latency are measured",
    "steps is a unit-cost proxy, not seconds or tokens",
    "conflict and death patterns are scripted by the scenario, not sampled from production",
    "a coordinated run that blocks an irreversible task is counted as incomplete on purpose",
)


def _task(row):
    if not isinstance(row, dict):
        raise ValueError("task must be an object")
    task_id = row.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task.id is required")
    steps = row.get("steps", 1)
    if type(steps) is not int or steps < 1:
        raise ValueError(f"task {task_id}: steps must be a positive int")
    return {"id": task_id.strip(), "steps": steps,
            "scope": normalize(row["scope"]) if row.get("scope") else "",
            "dies_at": row.get("dies_at"),
            "irreversible": bool(row.get("irreversible", False)),
            "approvers": row.get("approvers", 0)}


def _scenario(row):
    if not isinstance(row, dict) or not isinstance(row.get("tasks"), list) or not row["tasks"]:
        raise ValueError("scenario.tasks must be a non-empty list")
    tasks = [_task(item) for item in row["tasks"]]
    ids = [item["id"] for item in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("task IDs must be unique")
    return {"name": str(row.get("name", "unnamed")), "tasks": tasks,
            "threshold": row.get("threshold", 2)}


def _conflict_pairs(tasks):
    pairs = []
    for index, left in enumerate(tasks):
        for right in tasks[index + 1:]:
            if left["scope"] and right["scope"] and overlaps(left["scope"], right["scope"]):
                pairs.append((left["id"], right["id"]))
    return pairs


def _base(scenario):
    return {"scenario": scenario["name"], "tasks_total": len(scenario["tasks"]),
            "completed": 0, "duplicate_side_effects": 0,
            "unsafe_irreversible_executions": 0, "conflicts_present": len(_conflict_pairs(scenario["tasks"])),
            "conflicts_detected": 0, "recovery_attempted": 0, "recovery_succeeded": 0,
            "blocked": 0, "steps": 0, "evidence_recorded": 0, "human_interventions": 0}


def run_solo(scenario):
    """One unattended worker: no claim gate, no recovery, no second signature."""
    row = _base(scenario)
    for task in scenario["tasks"]:
        row["steps"] += task["steps"]
        if task["dies_at"] is not None and task["dies_at"] < task["steps"]:
            row["steps"] -= task["steps"] - task["dies_at"]
            continue  # work is lost; nobody notices or re-queues it
        if task["irreversible"]:
            row["unsafe_irreversible_executions"] += 1
        row["completed"] += 1
        row["evidence_recorded"] += 1
    row["duplicate_side_effects"] = len(_conflict_pairs(scenario["tasks"]))
    return row


def run_coordinated(scenario):
    """Claim gate, one-step evidence per task, reinforcement, and quorum."""
    row = _base(scenario)
    claimed = []
    for task in scenario["tasks"]:
        row["steps"] += 1  # claim + evidence write is not free
        row["steps"] += task["steps"]
        contested = any(task["scope"] and other["scope"] and overlaps(task["scope"], other["scope"])
                        for other in claimed)
        if contested:
            row["conflicts_detected"] += 1
            row["steps"] += 1  # serialise behind the existing claim instead of writing twice
        claimed.append(task)
        died = task["dies_at"] is not None and task["dies_at"] < task["steps"]
        if died:
            row["recovery_attempted"] += 1
            row["steps"] += task["steps"] - task["dies_at"]
            row["recovery_succeeded"] += 1
        if task["irreversible"] and task["approvers"] < scenario["threshold"]:
            row["blocked"] += 1
            continue  # refused for lack of independent approval; no unsafe effect
        row["completed"] += 1
        row["evidence_recorded"] += 1
    return row


def _rates(row):
    total = row["tasks_total"]
    return {
        **row,
        "completion_rate": round(row["completed"] / total, 4),
        "evidence_coverage": round(row["evidence_recorded"] / row["completed"], 4) if row["completed"] else 0.0,
        "conflict_recall": round(row["conflicts_detected"] / row["conflicts_present"], 4)
        if row["conflicts_present"] else None,
    }


def compare(scenario):
    """Run both strategies on one scenario and report the deltas."""
    prepared = _scenario(scenario)
    solo = _rates(run_solo(prepared))
    coordinated = _rates(run_coordinated(prepared))
    return {
        "schema": "agent-evolution-collab-bench-v1",
        "scenario": prepared["name"],
        "evidence": "SIMULATION_ONLY",
        "solo": solo,
        "coordinated": coordinated,
        "delta": {
            "completed": coordinated["completed"] - solo["completed"],
            "duplicate_side_effects": coordinated["duplicate_side_effects"] - solo["duplicate_side_effects"],
            "unsafe_irreversible_executions": (coordinated["unsafe_irreversible_executions"]
                                               - solo["unsafe_irreversible_executions"]),
            "steps": coordinated["steps"] - solo["steps"],
            "blocked": coordinated["blocked"] - solo["blocked"],
        },
        "limitations": list(LIMITATIONS),
    }


BUILTIN_SCENARIOS = (
    {"name": "overlapping-scopes", "threshold": 2, "tasks": [
        {"id": "alpha", "steps": 3, "scope": "src/pkg"},
        {"id": "beta", "steps": 2, "scope": "src/pkg/module.py"},
        {"id": "gamma", "steps": 1, "scope": "docs"},
    ]},
    {"name": "agent-dies-mid-task", "threshold": 2, "tasks": [
        {"id": "alpha", "steps": 5, "scope": "src/a", "dies_at": 2},
        {"id": "beta", "steps": 2, "scope": "src/b"},
    ]},
    {"name": "irreversible-upload", "threshold": 2, "tasks": [
        {"id": "alpha", "steps": 2, "scope": "dist/a.whl", "irreversible": True, "approvers": 1},
        {"id": "beta", "steps": 2, "scope": "dist/b.whl", "irreversible": True, "approvers": 2},
    ]},
    {"name": "mixed-pressure", "threshold": 2, "tasks": [
        {"id": "alpha", "steps": 4, "scope": "src/pkg"},
        {"id": "beta", "steps": 3, "scope": "src/pkg/deep", "dies_at": 1},
        {"id": "gamma", "steps": 2, "scope": "dist/out.whl", "irreversible": True, "approvers": 1},
        {"id": "delta", "steps": 1, "scope": "docs/readme.md"},
    ]},
)


def run_suite(scenarios=None):
    """Run the built-in (or supplied) scenarios and aggregate the totals."""
    rows = [compare(item) for item in (scenarios or BUILTIN_SCENARIOS)]
    totals = {}
    for side in ("solo", "coordinated"):
        totals[side] = {key: sum(row[side][key] for row in rows) for key in (
            "tasks_total", "completed", "duplicate_side_effects",
            "unsafe_irreversible_executions", "conflicts_present", "conflicts_detected",
            "recovery_attempted", "recovery_succeeded", "blocked", "steps")}
        totals[side]["completion_rate"] = round(
            totals[side]["completed"] / totals[side]["tasks_total"], 4)
    return {"schema": "agent-evolution-collab-bench-suite-v1",
            "evidence": "SIMULATION_ONLY",
            "scenarios": rows,
            "totals": totals,
            "limitations": list(LIMITATIONS),
            "real_world_numbers": "NOT_VERIFIED"}


def main(argv=None):
    """Print the built-in suite, or a scenario file supplied with ``--scenario-file``."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(prog="collab_bench", description=__doc__.splitlines()[0])
    parser.add_argument("--scenario-file", help="JSON file with one scenario or a list of them")
    parser.add_argument("--compact", action="store_true", help="single-line JSON")
    args = parser.parse_args(argv)
    try:
        if args.scenario_file:
            from pathlib import Path
            data = json.loads(Path(args.scenario_file).read_text(encoding="utf-8"))
            scenarios = data if isinstance(data, list) else [data]
            payload = run_suite(scenarios)
        else:
            payload = run_suite()
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
