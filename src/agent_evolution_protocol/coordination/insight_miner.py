"""Offline, read-only insight mining over already-recorded facts.

This is the missing consumer for the project's append-only evidence streams.
It does NOT decide anything: every finding is a `lead` bound to real files and
their sha256, and `verify()` recomputes the count from those files before any
proposal is allowed to exist. A local model may add an advisory note, but its
text can never reach `event_id`, `kind`, `evidence`, `severity` or `count`.

Deliberately socket-free (guarded by tests/test_insight_miner.py): the model
lives in `insight_proposer.py` and is loaded only when explicitly requested.
Absence or failure of the model is a fallback, not an outage.

Nothing here edits source, commits, promotes or deploys. The only write path is
`--apply`, which publishes an advisory-only proposal through the existing
`improvement.ingest()` evidence-bound journal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from . import improvement

DEFAULT_AUTHOR = "insight-miner"
NOTE = ("Findings are leads, not confirmed root causes. Evidence is frozen by the "
        "improvement journal; promotion still requires an independent reviewer.")

RULES = {
    "unclassified_failure": {
        "source": "command_memory", "min_count": 3, "min_ratio": 0.5,
        "summary": "已观测失败中未被规则库归类的比例偏高，说明关键词表可能缺本机错误模式。",
    },
    "repeated_failure_signature": {
        "source": "command_memory", "min_count": 2, "min_ratio": 0.0,
        "summary": "同一错误签名重复出现，值得检查是否存在未修的复现路径。",
    },
    "unverified_overlay": {
        "source": "overlay_audit", "min_count": 1, "min_ratio": 0.0,
        "summary": "弹窗审计存在 verified=false 的记录，处置动作未被证明。",
    },
    "failed_trajectory_step": {
        "source": "trajectories", "min_count": 1, "min_ratio": 0.0,
        "summary": "只读轨迹中存在非零退出码的步骤。",
    },
}

ADVISORY_FIELDS = {"title", "hypothesis", "checks"}
_FORBIDDEN = (
    re.compile(r"[A-Za-z]:[\\/]"),
    re.compile(r"/(?:mnt|usr|etc|home)/"),
    re.compile(r"\\\\"),
    re.compile(r"git|rm\s|del\s|powershell|pwsh|cmd\.exe|Invoke-Expression|"
               r"subprocess|curl|wget|shutil|--apply|--force", re.IGNORECASE),
    re.compile(r"[\x00-\x1f\x7f]"),
)


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _jsonl_rows(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _paths(workspace: Path, coord_root: Path) -> dict[str, list[Path]]:
    overlay = workspace / "overlay_audit.jsonl"
    return {
        "command_memory": sorted((coord_root / "command_memory" / "observations").glob("*.json")),
        "overlay_audit": [overlay] if overlay.is_file() else [],
        "trajectories": sorted((coord_root / "trajectories").glob("*.json")),
    }


def _scanned_counts(paths: dict[str, list[Path]]) -> dict[str, int]:
    counts = {"command_memory": len(paths["command_memory"]),
              "overlay_audit": 0, "trajectories": 0}
    for path in paths["overlay_audit"]:
        counts["overlay_audit"] += len(_jsonl_rows(path))
    for path in paths["trajectories"]:
        steps = _read_json(path).get("steps")
        counts["trajectories"] += len(steps) if isinstance(steps, list) else 0
    return counts


def _measure(rule: str, files: list[Path]) -> dict:
    """Pure recomputation from evidence files; used by both scan() and verify()."""
    if rule == "unclassified_failure":
        rows = [_read_json(p) for p in files]
        count = sum(r.get("outcome") == "observed_failure" and r.get("category") == "unknown"
                    for r in rows)
        return {"count": count, "scanned": len(files), "key": "unknown-category",
                "evidence_files": [str(p) for p in files]}

    if rule == "repeated_failure_signature":
        groups: dict[str, list[Path]] = {}
        for path in files:
            row = _read_json(path)
            if row.get("outcome") == "observed_failure":
                groups.setdefault(str(row.get("signature", "")), []).append(path)
        key, members = max(groups.items(), key=lambda item: (len(item[1]), item[0]),
                           default=("", []))
        return {"count": len(members), "scanned": sum(len(v) for v in groups.values()),
                "key": key, "evidence_files": [str(p) for p in files]}

    if rule == "unverified_overlay":
        rows = [row for path in files for row in _jsonl_rows(path)]
        count = sum(row.get("verified") is not True for row in rows)
        return {"count": count, "scanned": len(rows), "key": "verified-false",
                "evidence_files": [str(p) for p in files]}

    if rule == "failed_trajectory_step":
        steps = []
        for path in files:
            candidate = _read_json(path).get("steps")
            if isinstance(candidate, list):
                steps.extend(candidate)
        count = sum(isinstance(step, dict) and step.get("exit_code") not in (0, None)
                    for step in steps)
        return {"count": count, "scanned": len(steps), "key": "nonzero-exit",
                "evidence_files": [str(p) for p in files]}

    raise ValueError(f"unknown rule: {rule}")


def _triggered(rule: str, measured: dict, *, min_count=None, ratio=None) -> bool:
    spec = RULES[rule]
    floor = spec["min_count"] if min_count is None else min_count
    threshold = spec["min_ratio"] if ratio is None else ratio
    if measured["count"] < floor or measured["count"] <= 0:
        return False
    if threshold <= 0:
        return True
    return measured["scanned"] > 0 and measured["count"] / measured["scanned"] >= threshold


def _finding(rule: str, measured: dict) -> dict:
    files = sorted(measured["evidence_files"])
    identity = _hash({"rule": rule, "key": measured["key"], "evidence_files": files})[:24]
    return {
        "finding_id": identity, "rule": rule, "key": measured["key"],
        "count": measured["count"], "scanned": measured["scanned"],
        "evidence_files": files,
        "evidence_sha256": [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in files],
        "summary": (f"{RULES[rule]['summary']} "
                    f"(rule={rule}; count={measured['count']}; scanned={measured['scanned']})"),
        "severity": "lead", "model_generated": False,
    }


def scan(workspace: Path, *, coord_root: Path | None = None,
         min_count: int | None = None, ratio: float | None = None) -> dict:
    """Read-only scan. Always reports per-source counts so a clean machine and a
    broken scanner cannot produce the same output."""
    workspace = Path(workspace).resolve()
    root = Path(coord_root).resolve() if coord_root is not None else workspace / "coordination"
    paths = _paths(workspace, root)
    findings, measurements = [], {}
    for rule, spec in RULES.items():
        measured = _measure(rule, paths[spec["source"]])
        triggered = _triggered(rule, measured, min_count=min_count, ratio=ratio)
        # Always report the measurement: "no findings" must be distinguishable
        # from "scanner broken" and from "threshold not reached".
        measurements[rule] = {
            "count": measured["count"], "scanned": measured["scanned"],
            "triggered": triggered,
            "min_count": spec["min_count"] if min_count is None else min_count,
            "min_ratio": spec["min_ratio"] if ratio is None else ratio,
        }
        if triggered:
            findings.append(_finding(rule, measured))
    findings.sort(key=lambda item: item["finding_id"])
    return {"workspace": str(workspace), "scanned": _scanned_counts(paths),
            "rules_evaluated": len(RULES), "measurements": measurements,
            "findings": findings, "note": NOTE}


def verify(finding: dict, *, min_count: int | None = None,
           ratio: float | None = None) -> dict:
    """Recompute from the evidence files. Any drift rejects the finding."""
    rule = finding.get("rule")
    if rule not in RULES:
        return {"verified": False, "reason": "unknown-rule", "recomputed_count": 0}
    if finding.get("severity") != "lead":
        return {"verified": False, "reason": "severity-escalation-rejected", "recomputed_count": 0}
    if finding.get("model_generated") is not False:
        return {"verified": False, "reason": "finding-marked-as-model-generated", "recomputed_count": 0}
    files = finding.get("evidence_files")
    digests = finding.get("evidence_sha256")
    if not isinstance(files, list) or not files or not isinstance(digests, list) or len(files) != len(digests):
        return {"verified": False, "reason": "evidence-list-malformed", "recomputed_count": 0}
    for path, digest in zip(files, digests):
        target = Path(path)
        if not target.is_file():
            return {"verified": False, "reason": "missing-evidence", "recomputed_count": 0}
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            return {"verified": False, "reason": "evidence-changed", "recomputed_count": 0}
    measured = _measure(rule, [Path(p) for p in files])
    if measured["count"] != finding.get("count") or measured["scanned"] != finding.get("scanned"):
        return {"verified": False, "reason": "count-mismatch",
                "recomputed_count": measured["count"]}
    if measured["key"] != finding.get("key"):
        return {"verified": False, "reason": "key-mismatch", "recomputed_count": measured["count"]}
    if not _triggered(rule, measured, min_count=min_count, ratio=ratio):
        return {"verified": False, "reason": "no-longer-triggered",
                "recomputed_count": measured["count"]}
    return {"verified": True, "reason": "recomputed-from-evidence",
            "recomputed_count": measured["count"]}


def validate_advisory(value) -> dict | None:
    """Strict allowlist. Unknown fields, absolute paths, commands and control
    characters are rejected rather than sanitised; a sanitised command is still
    a command someone might read as an instruction."""
    if not isinstance(value, dict) or set(value) != ADVISORY_FIELDS:
        return None
    title, hypothesis, checks = value["title"], value["hypothesis"], value["checks"]
    if not isinstance(title, str) or not 1 <= len(title) <= 200:
        return None
    if not isinstance(hypothesis, str) or not 1 <= len(hypothesis) <= 300:
        return None
    if not isinstance(checks, list) or not 1 <= len(checks) <= 5:
        return None
    if any(not isinstance(item, str) or not 1 <= len(item) <= 120 for item in checks):
        return None
    for text in (title, hypothesis, *checks):
        if any(pattern.search(text) for pattern in _FORBIDDEN):
            return None
    return {"title": title, "hypothesis": hypothesis, "checks": list(checks)}


def build_event(finding: dict, *, author: str = DEFAULT_AUTHOR, advisory=None) -> dict:
    """Produce the exact three-field event `improvement.ingest()` accepts.
    Model text may only be appended to the `change` prose."""
    change = finding["summary"]
    clean = validate_advisory(advisory) if advisory is not None else None
    if clean is not None:
        change += ("\n[model_generated advisory; not evidence, not a decision]\n"
                   f"标题：{clean['title']}\n假设：{clean['hypothesis']}\n"
                   "建议检查：" + "；".join(clean["checks"]))
    return {
        "event_id": "INSIGHT-" + finding["finding_id"],
        "kind": "TASK_FAILED",
        "improvement": {
            "author": author,
            "change": change,
            "acceptance": ("重新运行 insight-miner verify 复算证据 sha256 与计数；"
                           "由独立 reviewer 判定是否立项；测试通过不等于晋升。"),
            "rollback": "删除本提案即可；本提案不修改源码、配置或运行时状态。",
            "scope": "advisory-only; no source changes",
            "evidence": list(finding["evidence_files"]),
        },
    }


def _publish_raw(path: Path, data: bytes) -> None:
    """Publish without overwriting a concurrent winner (same-volume hard link)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def _publish(path: Path, value: dict) -> None:
    _publish_raw(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def _snapshot(root: Path, finding: dict) -> list[str]:
    """Freeze append-only evidence into immutable copies.

    `overlay_audit.jsonl` keeps growing; freezing the live file would make every
    later `improvement.advance()` fail with "frozen evidence changed" forever.
    A snapshot keeps the proposal's evidence both recomputable and stable.
    """
    folder = root / "insights" / "evidence" / finding["finding_id"]
    targets = []
    for index, source in enumerate(finding["evidence_files"]):
        target = folder / f"{index:04d}-{Path(source).name}"
        _publish_raw(target, Path(source).read_bytes())
        targets.append(str(target))
    return targets


def mine(workspace: Path, *, apply: bool = False, author: str = DEFAULT_AUTHOR,
         proposer=None, coord_root: Path | None = None,
         min_count: int | None = None, ratio: float | None = None) -> dict:
    """scan -> verify -> optional advisory -> (only with apply) ingest.

    Default is a dry run that writes nothing. `proposer` is an injected callable
    `propose(finding) -> advisory | None`; this module never loads it itself.
    """
    workspace = Path(workspace).resolve()
    root = Path(coord_root).resolve() if coord_root is not None else workspace / "coordination"
    report = scan(workspace, coord_root=root, min_count=min_count, ratio=ratio)
    verified, rejected, ingested = [], [], []
    advisory_used = 0
    status = "disabled" if proposer is None else "ok"
    for finding in report["findings"]:
        check = verify(finding, min_count=min_count, ratio=ratio)
        if not check["verified"]:
            rejected.append({"finding_id": finding["finding_id"], "reason": check["reason"]})
            continue
        advisory = None
        if proposer is not None:
            try:
                candidate = proposer(finding)
            except Exception as exc:  # a dead model costs the note, not the finding
                status = f"failed:{exc.__class__.__name__}"
            else:
                if candidate is None:
                    if status == "ok":
                        status = "abstained"
                elif validate_advisory(candidate) is None:
                    status = "rejected-advisory"
                else:
                    advisory = validate_advisory(candidate)
                    advisory_used += 1
        verified.append({"finding_id": finding["finding_id"], "rule": finding["rule"],
                         "count": finding["count"], "advisory_attached": advisory is not None})
        if not apply:
            continue
        # Once a snapshot is published, a changed source must not silently
        # redefine an existing proposal. Report the drift and let a human re-open it.
        snapshot_dir = root / "insights" / "evidence" / finding["finding_id"]
        if snapshot_dir.is_dir():
            existing = sorted(p for p in snapshot_dir.iterdir() if p.is_file())
            if existing and (len(existing) != len(finding["evidence_files"]) or any(
                    Path(source).read_bytes() != prior.read_bytes()
                    for source, prior in zip(finding["evidence_files"], existing))):
                rejected.append({"finding_id": finding["finding_id"],
                                 "reason": "evidence-drift-since-snapshot"})
                continue
        snapshot = _snapshot(root, finding)
        applied = {**finding, "evidence_files": snapshot,
                   "evidence_sha256": [hashlib.sha256(Path(p).read_bytes()).hexdigest()
                                      for p in snapshot]}
        snapshot_check = verify(applied, min_count=min_count, ratio=ratio)
        if not snapshot_check["verified"]:
            rejected.append({"finding_id": finding["finding_id"],
                             "reason": "snapshot-" + snapshot_check["reason"]})
            continue
        _publish(root / "insights" / (finding["finding_id"] + ".json"),
                 {**finding, "advisory": advisory, "verify": check,
                  "snapshot_verify": snapshot_check, "evidence_snapshot": snapshot,
                  "model_generated": False})
        event = build_event(applied, author=author, advisory=advisory)
        event_path = root / "insight_events" / (event["event_id"] + ".json")
        _publish(event_path, event)  # stable: replay must not change the payload
        result = improvement.ingest(root, event_path)
        ingested.append({"event_id": event["event_id"], "id": result["id"],
                         "state": result["state"],
                         "deployment_performed": result["deployment_performed"]})
    return {
        "workspace": report["workspace"], "scanned": report["scanned"],
        "rules_evaluated": report["rules_evaluated"],
        "measurements": report["measurements"],
        "candidates": len(report["findings"]), "verified": verified,
        "rejected": rejected, "advisory_used": advisory_used,
        "model_status": status, "applied": bool(apply), "ingested": ingested,
        "deployment_performed": False, "note": NOTE,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--coord-root", type=Path)
    parser.add_argument("--apply", action="store_true", help="publish advisory-only proposals")
    parser.add_argument("--propose", action="store_true",
                        help="opt in to an ephemeral local model advisory (default: rules only)")
    parser.add_argument("--backend", choices=("ollama", "lmstudio"), default="lmstudio")
    parser.add_argument("--model", default="", help="model key; empty uses the backend default")
    parser.add_argument("--ttl", type=int, default=60,
                        help="idle seconds before LM Studio unloads the model by itself")
    parser.add_argument("--gpu", default="off", help="off / max / numeric offload ratio")
    parser.add_argument("--context-length", type=int, default=2048)
    parser.add_argument("--min-count", type=int)
    parser.add_argument("--ratio", type=float)
    args = parser.parse_args(argv)
    common = dict(workspace=args.workspace, apply=args.apply,
                  coord_root=args.coord_root, min_count=args.min_count, ratio=args.ratio)
    if not args.propose:
        report = mine(**common)
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0

    import importlib
    proposer_module = importlib.import_module(
        "agent_evolution_protocol.coordination.insight_proposer")
    session_module = importlib.import_module(
        "agent_evolution_protocol.coordination.local_model_session")
    default_model = (proposer_module.LMSTUDIO_MODEL if args.backend == "lmstudio"
                     else proposer_module.DEFAULT_MODEL)
    plan = session_module.Plan(backend=args.backend, model=args.model or default_model,
                               ttl_seconds=args.ttl, gpu=args.gpu,
                               context_length=args.context_length)
    session = session_module.EphemeralModelSession(plan)
    try:
        with session:
            propose_fn = proposer_module.local_model_proposer(
                backend=args.backend, model=plan.model)
            report = mine(**common, proposer=propose_fn)
        report["model_session"] = {"backend": args.backend, "model": plan.model,
                                   "ttl_seconds": plan.ttl_seconds,
                                   "gpu": plan.gpu,
                                   "commands_run": session.commands_run,
                                   "unloaded": not session.loaded}
    except (session_module.ModelUnavailable, OSError, ValueError) as exc:
        # Never start a resident service to make the model work. Rules are enough.
        report = mine(**common)
        report["model_status"] = "unavailable:" + exc.__class__.__name__
        report["model_note"] = (f"{exc}; kept the rule-only result. "
                                "No server was started and no model is resident.")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
