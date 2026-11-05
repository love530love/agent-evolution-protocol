"""One-shot coordinator tick: wire the existing single-step chains together.

This module is *not* a scheduler.  Three bounded single steps already exist and
are owned elsewhere:

* ``campaign_tick.tick`` -- presence reconciliation, delegation mailbox,
  journal ingestion and ``campaign_cycle.reconcile`` (ACK / work deadlines,
  capacity release, rescue assignment);
* ``mission_pulse.pulse`` -- persisted ``missions/M-*.json`` ready stages,
  gated and recomputed, queued as ``DEPOSITED`` orders;
* ``federation/supervisor.step`` -- settle, alert, ``engine.tick`` scheduling,
  write-scope lease, then ``commander.tick`` to actually send.  When a DAG
  kernel context is unavailable, ``commander.tick`` is used directly instead.

The order matters: settle/deadlines first, then mission intake, then exactly
one dispatch path last.  ``supervisor.step`` already calls ``commander.tick``
internally, so **a failed federation step must never fall back to a second
dispatch** -- that would be a duplicate delivery, not a retry.

No model, no timer, no daemon, no subprocess, no desktop automation of its
own, no system task.  Default is read-only; ``--apply`` is the only write path.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import bridge, campaign_cycle, campaign_tick, commander, mission_pulse, presence

NOTE = (
    "One-shot wiring layer: three existing single steps in a fixed order, and "
    "exactly one dispatch path. Not a scheduler. Default is read-only. "
    "--apply is the only path that writes. A failed federation step never "
    "falls back to commander.tick because that would duplicate a delivery."
)

DEFAULT_SENDER = "coordinator"
ERRORS = (OSError, ValueError, KeyError, TypeError, RuntimeError)


def plan(*, federation_ready: bool = False) -> list[dict]:
    """What an ``--apply`` run would call, without calling any of it."""
    dispatch = ("federation/supervisor.step (already contains commander.tick)"
                if federation_ready else "commander.tick")
    return [
        {"stage": "settle_and_deadlines", "callable": "campaign_tick.tick", "writes": True,
         "note": "presence.reconcile -> team_dispatch.process -> "
                 "command_memory.ingest_journal -> campaign_cycle.reconcile"},
        {"stage": "mission_intake", "callable": "mission_pulse.pulse(apply=True)",
         "writes": True,
         "note": "persisted ready stages -> DEPOSITED orders only, never delivery"},
        {"stage": "dispatch", "callable": dispatch, "writes": True, "exclusive": True,
         "note": "exactly one path; a failed federation step is not retried here"},
        {"stage": "federation", "callable": "federation/supervisor.step", "writes": False,
         "status": "ready" if federation_ready else "skipped",
         "reason": "" if federation_ready else "FEDERATION_CONTEXT_MISSING"},
    ]


def _mission_preview(root: Path) -> dict:
    row = mission_pulse.pulse(root, apply=False)
    return {"scanned": row["scanned"], "candidates": row["candidates"],
            "verified": len(row["verified"]), "rejected": len(row["rejected"])}


def _campaign_preview(root: Path) -> dict:
    row = campaign_cycle.status(root)
    return {"missions": len(row["missions"]), "next_due_at": row["next_due_at"]}


def _commander_preview(root: Path) -> dict:
    return {"orders": len(commander.status(root)["orders"])}


def preview(root: Path) -> dict:
    """Read-only snapshot.  A broken source is reported, never guessed around."""
    out: dict = {"next_due_source": "campaign_tick.next_due"}
    sources = (
        ("mission", lambda: _mission_preview(root)),
        ("campaign", lambda: _campaign_preview(root)),
        ("presence", lambda: presence.snapshot(root)["strength"]),
        ("commander", lambda: _commander_preview(root)),
        ("next_due_at", lambda: campaign_tick.next_due(root)["next_due_at"]),
    )
    for key, read in sources:
        try:
            out[key] = read()
        except ERRORS as exc:
            out[key] = {"unavailable": f"{type(exc).__name__}: {exc}"}
    return out


def _ok(stage: str, callable_name: str, result: dict) -> dict:
    return {"stage": stage, "callable": callable_name, "status": "ok", "result": result}


def _failed(stage: str, callable_name: str, exc: Exception) -> dict:
    return {"stage": stage, "callable": callable_name, "status": "failed",
            "error": f"{type(exc).__name__}: {exc}"}


def tick(root, *, apply: bool = False, bridge_root=None, kernel_root=None,
         workspace_id: str = "", agents=None, sender: str = DEFAULT_SENDER) -> dict:
    """One bounded pass.  ``apply=True`` is the only path that can write.

    ``kernel_root``, ``workspace_id`` and a non-empty ``agents`` list must all
    be present to use the federation path.  When they are not, that fact is
    recorded as ``skipped`` -- never as a step that ran -- and the plain
    ``commander.tick`` path is used.
    """
    root = Path(root)
    bridge_root = Path(bridge_root) if bridge_root is not None else bridge.ROOT
    names = [str(name).strip() for name in (agents or []) if str(name).strip()]
    federation_ready = bool(kernel_root is not None and workspace_id and names)

    report: dict = {
        "root": str(root), "applied": bool(apply),
        "stages": [], "skipped": [], "plan": plan(federation_ready=federation_ready),
        "dispatch_path": "none", "halted": "", "writes_performed": 0,
        "models_loaded": 0, "processes_started": 0, "ui_touched": False,
        "deployment_performed": False, "system_tasks_registered": False,
        "note": NOTE,
    }

    if not apply:
        report["preview"] = preview(root)
        return report

    # 1. settle first: deadlines, presence, mailbox and knowledge ingestion.
    #    A failure here leaves the state unknown, so nothing may be queued or
    #    sent afterwards.  This is fail-closed on purpose.
    try:
        result = campaign_tick.tick(root, bridge_root)
        report["stages"].append(_ok("settle_and_deadlines", "campaign_tick.tick", result))
        report["writes_performed"] += 1
    except ERRORS as exc:
        report["stages"].append(_failed("settle_and_deadlines", "campaign_tick.tick", exc))
        report["halted"] = "SETTLE_FAILED"
        return report

    # 2. mission intake: only queued as DEPOSITED, never delivered.
    try:
        result = mission_pulse.pulse(root, apply=True, sender=sender)
        report["stages"].append(_ok("mission_intake", "mission_pulse.pulse", result))
        report["writes_performed"] += 1
    except ERRORS as exc:
        report["stages"].append(_failed("mission_intake", "mission_pulse.pulse", exc))
        report["halted"] = "MISSION_FAILED"
        return report

    # 3. exactly one dispatch path, chosen before anything is sent.
    if federation_ready:
        report["dispatch_path"] = "federation_step"
        try:
            from agent_evolution_protocol.federation import supervisor
            adapters = supervisor.build_adapters(coord_root=root, agents=names,
                                                 workspace_id=workspace_id)
            result = supervisor.step(Path(kernel_root), root, workspace_id, adapters,
                                     perceive_first=False)
            report["stages"].append(_ok("dispatch", "federation/supervisor.step", result))
            report["writes_performed"] += 1
        except ERRORS as exc:
            # Never fall back: step() may already have called commander.tick().
            report["stages"].append(_failed("dispatch", "federation/supervisor.step", exc))
    else:
        report["skipped"].append({"stage": "federation",
                                  "callable": "federation/supervisor.step",
                                  "status": "skipped",
                                  "reason": "FEDERATION_CONTEXT_MISSING"})
        report["dispatch_path"] = "commander_tick"
        try:
            result = commander.tick(root, perceive_first=False)
            report["stages"].append(_ok("dispatch", "commander.tick", result))
            report["writes_performed"] += 1
        except ERRORS as exc:
            report["stages"].append(_failed("dispatch", "commander.tick", exc))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One-shot, model-free coordinator tick")
    parser.add_argument("--coord-root", type=Path, default=Path.cwd() / "coordination",
                        help="coordination runtime root (default: ./coordination)")
    parser.add_argument("--bridge-root", type=Path, default=bridge.ROOT)
    parser.add_argument("--apply", action="store_true",
                        help="actually run the three existing single steps")
    parser.add_argument("--sender", default=DEFAULT_SENDER)
    parser.add_argument("--kernel-root", type=Path, default=None,
                        help="federation DAG root; requires --workspace and --agents")
    parser.add_argument("--workspace", default="", help="workspace id for the DAG path")
    parser.add_argument("--agents", default="", help="comma-separated agent names")
    args = parser.parse_args(argv)
    names = [part.strip() for part in args.agents.split(",") if part.strip()]
    report = tick(args.coord_root, apply=args.apply, bridge_root=args.bridge_root,
                  kernel_root=args.kernel_root, workspace_id=args.workspace,
                  agents=names, sender=args.sender)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
