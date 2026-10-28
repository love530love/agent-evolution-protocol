"""One read-only next step for a route packet; never execute or parse shell text.

Integration: result["next_action"] = build_next_action(
    result, root=workspace, coord_root=croot)

The caller supplies the already selected roots. This module does not discover a
workspace, verify CLI availability, read state, send a handoff, or claim work.
"""
from __future__ import annotations

import os
from pathlib import Path
import re


SCENARIOS = frozenset({"project-orientation", "task-join", "takeover", "review-only",
    "isolated-exploration", "shared-resource-lock", "stalled-recovery", "wake-agent",
    "status-report", "cli-less-fallback"})
TASK_SCENARIOS = frozenset({"task-join", "takeover", "isolated-exploration",
    "shared-resource-lock", "stalled-recovery", "wake-agent"})
RECOVERY_STATES = frozenset({"unclaimed", "unknown", "waiting", "terminal", "active",
    "suspected-stale", "claim-pending-or-unreadable"})
RESERVED_IDS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
                *(f"LPT{i}" for i in range(10))}


def _path_argument(value):
    try:
        value = os.fspath(value)
    except TypeError:
        return None
    return value if isinstance(value, str) and value.strip() and "\0" not in value else None


def _identifier(value):
    return (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value)
            and value.upper() not in RESERVED_IDS)


def build_next_action(result: dict, root: str | Path | None, coord_root: str | Path | None) -> dict:
    """Build one argv or a concrete request for missing input/manual assistance.

    argv is a process argument list, never a shell command. Do not join it into
    a string or execute command-looking text from first_commands, checkpoints or
    blockers. A completed read does not complete the requested collaboration.
    """
    workspace, coordination = _path_argument(root), _path_argument(coord_root)
    scenario = result.get("scenario")
    task, agent = result.get("task"), result.get("agent")
    recovery = result.get("recovery")
    recovery_state = recovery.get("status") if isinstance(recovery, dict) else None
    missing = []

    def require(condition, field, reason):
        if not condition:
            missing.append({"field": field, "reason": reason})

    require(workspace is not None, "workspace", "Supply the intended workspace; current directory is not inferred.")
    require(coordination is not None, "coord_root", "Supply the authoritative coordination directory.")
    require(scenario in SCENARIOS, "scenario", "Select one recognized collaboration scenario.")
    require(recovery_state in RECOVERY_STATES, "recovery.status", "Obtain a route snapshot with a recognized recovery state.")
    require(result.get("requires_clarification") is False and result.get("ambiguity") is False,
            "intent_scope", "Resolve missing or ambiguous route scope before choosing a next step.")
    if task or scenario in TASK_SCENARIOS:
        require(bool(_identifier(task)), "task", "Supply one valid task ID; no task or candidate ID is invented.")
    if scenario in {"task-join", "wake-agent"}:
        require(bool(_identifier(agent)), "agent", "Supply the current agent ID for its own unread digest.")
    if scenario == "wake-agent":
        require(bool(_identifier(result.get("target_agent"))), "target_agent",
                "Identify the intended recipient before planning a wake; this step does not send one.")

    # Do not use inspect/join here: their ClusterKernel constructor creates the
    # kernel directory. These two endpoints perform only read operations.
    digest = scenario in {"task-join", "wake-agent"} and recovery_state not in {
        "unknown", "claim-pending-or-unreadable"} and not result.get("blockers")
    action = "read_current_agent_digest" if digest else "read_coordination_status"
    argv = None
    if not missing:
        argv = ["aep", "--coord-root", coordination]
        argv += ["coord-digest", "--agent", agent] if digest else ["coord-status"]

    files = []
    if workspace is not None:
        files.append(str(Path(workspace) / "AEP_JOIN.md"))
    if coordination is not None and _identifier(task):
        files += [str(Path(coordination) / "claims" / task / "owner.json"),
                  str(Path(coordination) / "task_states" / f"{task}.json")]
    fallback = {
        "kind": "manual_handoff_request", "required_capability": "file_read",
        "files": files, "files_verified": False, "automatic_dispatch": False,
        "request": "Read available handoff files; ask a command-capable collaborator to run the single read-only argv and return its JSON result. Report missing files or command errors.",
        "requested_argv": list(argv) if argv else None,
        "missing_inputs": [item["field"] for item in missing],
    }
    file_only = scenario == "cli-less-fallback"
    return {
        "schema": "agent-evolution-next-action-v1",
        "kind": "needs_input" if missing else "file_only" if file_only else "command",
        "action": "clarify_route_inputs" if missing else "prepare_manual_handoff" if file_only else action,
        "argv": None if file_only else argv, "cwd": workspace, "shell": False,
        "read_only": True, "execution_started": False,
        "required_capability": "clarification" if missing else "file_read" if file_only else "aep_cli",
        "capability_verified": False,
        "preconditions": [
            {"condition": "route_inputs_complete", "satisfied": not missing},
            {"condition": "required_capability_available", "satisfied": None},
        ],
        "placeholders": missing,
        "state_resolution_required": recovery_state in {"unknown", "claim-pending-or-unreadable"},
        "blockers": list(result.get("blockers", [])),
        "completion": "Return the read result or exact error. No task was claimed, resumed, executed, sent, or completed by this guidance.",
        "fallback": fallback,
    }
