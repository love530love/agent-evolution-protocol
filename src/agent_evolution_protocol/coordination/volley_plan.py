"""Read-only multi-wave package planner with repeat-failure ceasefire.

The output is a proposal, never an upload authorization or executor.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from agent_evolution_protocol.coordination.battle_orders import status as battle_order_status


GATES = ("rule_review", "local_gate", "cross_platform_review", "package_review", "independent_reviewer")


def _load_array(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ValueError(f"JSON array of objects required: {path}")
    return data


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _zip_ok(path: Path) -> bool:
    try:
        with ZipFile(path) as archive:
            return bool(archive.namelist()) and archive.testzip() is None
    except (BadZipFile, OSError, ValueError):
        return False


def _signature(row: dict) -> tuple[str, str, str]:
    return (str(row.get("task", "")).upper(), str(row.get("chip", "")).lower(),
            " ".join(str(row.get("failure_signature", "")).lower().split()))


def plan(manifest_file: Path, results_file: Path, *, quota_remaining: int,
         reserve: int = 0, wave_size: int = 3, coord_root: Path | None = None) -> dict:
    if quota_remaining < 0 or reserve < 0 or not 1 <= wave_size <= 20:
        raise ValueError("invalid quota, reserve or wave_size")
    candidates = _load_array(manifest_file)
    results = _load_array(results_file)
    seen_ids: set[str] = set()
    failure_submissions: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    unknown_tasks: set[str] = set()
    submitted_candidates: set[str] = set()
    for row in results:
        if row.get("candidate_id"):
            submitted_candidates.add(str(row["candidate_id"]))
        if str(row.get("status", "")).upper() == "UNKNOWN":
            unknown_tasks.add(str(row.get("task", "")).upper())
        key = _signature(row)
        if all(key) and str(row.get("status", "")).lower() == "failed" and row.get("submission_id"):
            failure_submissions[key].add(str(row["submission_id"]))
    ceasefires = [
        {"task": task, "chip": chip, "signature": signature, "submissions": sorted(ids)}
        for (task, chip, signature), ids in sorted(failure_submissions.items()) if len(ids) >= 2
    ]
    seen_shas: set[str] = set()
    eligible = []
    held = []
    for item in candidates:
        cid = str(item.get("candidate_id", ""))
        task = str(item.get("task", "")).upper()
        agent = str(item.get("agent", ""))
        declared_sha = str(item.get("sha256", "")).lower()
        if not cid or not task or not agent or cid in seen_ids:
            raise ValueError("each candidate needs a unique candidate_id, task and agent")
        seen_ids.add(cid)
        reasons = []
        path = Path(str(item.get("package", "")))
        if not path.is_file() or not _zip_ok(path):
            reasons.append("package_missing_or_invalid_zip")
        elif _sha(path) != declared_sha:
            reasons.append("package_sha_mismatch")
        if declared_sha in seen_shas:
            reasons.append("duplicate_package_sha")
        seen_shas.add(declared_sha)
        if cid in submitted_candidates:
            reasons.append("candidate_already_submitted")
        if any(not item.get(gate) for gate in GATES):
            reasons.append("evidence_gate_incomplete")
        if item.get("independent_reviewer") == agent:
            reasons.append("reviewer_is_author")
        if task in unknown_tasks:
            reasons.append("unreconciled_unknown_submission")
        if coord_root is not None and battle_order_status(coord_root, task)["active_holds"]:
            reasons.append("active_battle_ceasefire")
        affected = [incident for incident in ceasefires if incident["task"] == task]
        for incident in affected:
            fix = item.get("fixes", {})
            if not isinstance(fix, dict) or not fix.get(incident["signature"]):
                reasons.append("repeat_failure_ceasefire")
            elif not item.get("regression_report"):
                reasons.append("fix_missing_regression_report")
        entry = {"candidate_id": cid, "task": task, "agent": agent,
                 "package": str(path), "sha256": declared_sha,
                 "priority": int(item.get("priority", 0)), "reasons": sorted(set(reasons))}
        (held if reasons else eligible).append(entry)

    slots = max(0, quota_remaining - reserve)
    eligible.sort(key=lambda row: (-row["priority"], row["task"], row["candidate_id"]))
    waves = []
    scheduled = []
    scheduled_tasks = set()
    reload_wait = []
    remaining = eligible[:]
    while remaining and len(scheduled) < slots:
        wave = []
        wave_tasks = set()
        for row in remaining[:]:
            if len(wave) >= wave_size or len(scheduled) + len(wave) >= slots:
                break
            if row["task"] in scheduled_tasks:
                reload_wait.append(row)
                remaining.remove(row)
                continue
            if row["task"] in wave_tasks:
                continue
            wave.append({**row, "requires_fresh_result_before_same_task_next_candidate": True})
            wave_tasks.add(row["task"])
            remaining.remove(row)
        if not wave:
            break
        scheduled.extend(wave)
        scheduled_tasks.update(wave_tasks)
        waves.append({"number": len(waves) + 1, "candidates": wave,
                      "gate": "Recheck live quota, interval, same-task latest result, lease and idempotency before each actual upload."})
    for row in remaining[:]:
        if row["task"] in scheduled_tasks:
            reload_wait.append(row)
            remaining.remove(row)
    return {"waves": waves, "held": held, "ceasefires": ceasefires,
            "reload_wait": reload_wait, "unscheduled": remaining, "available_slots": slots,
            "status": "PROPOSAL_ONLY_NOT_AUTHORIZED",
            "note": "Only one candidate per task is planned until its result arrives; reload_wait is not an automatic next shot. Recompute after every official result. Same-signature repeat failures ceasefire the affected task until a reviewed fix and regression report exist. This tool performs no upload."}
