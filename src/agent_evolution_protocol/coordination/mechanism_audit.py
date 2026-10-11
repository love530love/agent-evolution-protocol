"""Evidence-based trigger for a reversible coordination-process experiment."""

from __future__ import annotations

import json
from pathlib import Path


METRICS = {
    "first_experiment_minutes": "lower",
    "cross_review_coverage": "higher",
    "repeat_error_rate": "lower",
    "information_gain_per_upload": "higher",
}


def audit(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) < 2:
        raise ValueError("need at least two ordered task metrics")
    older, newer = data[-2:]
    for entry in (older, newer):
        if not isinstance(entry, dict) or not entry.get("task") or not entry.get("evidence_paths"):
            raise ValueError("each task needs task and evidence_paths")
        for name in METRICS:
            if not isinstance(entry.get(name), (int, float)) or isinstance(entry[name], bool) or entry[name] < 0:
                raise ValueError(f"invalid {name}")
        if not 0 <= entry["cross_review_coverage"] <= 1 or not 0 <= entry["repeat_error_rate"] <= 1:
            raise ValueError("rates must be between 0 and 1")
    improved = []
    stalled = []
    for name, direction in METRICS.items():
        good = newer[name] < older[name] if direction == "lower" else newer[name] > older[name]
        (improved if good else stalled).append(name)
    overhead = newer.get("coordination_overhead_pct")
    if overhead is not None and (not isinstance(overhead, (int, float)) or not 0 <= overhead <= 100):
        raise ValueError("invalid coordination_overhead_pct")
    trigger = len(stalled) >= 3 or (overhead is not None and overhead > 20)
    return {
        "tasks": [older["task"], newer["task"]], "improved": improved, "stalled": stalled,
        "trigger_mechanism_challenge": trigger,
        "suggestion": "Open a reversible mechanism challenge card: failing stage, one small change, comparator, rollback condition and tests. Do not self-modify production automation without review." if trigger else "Keep current process; compare again after the next task.",
        "evidence_paths": [older["evidence_paths"], newer["evidence_paths"]],
        "note": "The metrics are operator-supplied; this audit does not verify platform results or modify code.",
    }
