"""Turn a mailbox message into an assigned task, not a notification.

The complaint this module answers is precise: a message that says "please look
at X" leaves the receiver to decide whether it owns anything.  Nothing in the
workspace records that work exists, no lease covers the files, and no one can
tell whether the request was accepted.  Three separate facts were missing:

* **existence** — the workspace DAG had no node, so the scheduler could not
  see the work at all;
* **ownership** — no claim covered the write scope, so two agents could start
  editing the same file;
* **acceptance** — an ACK token proves a message was read, never that a task
  was taken.

:func:`assign` writes all three in one call and returns a receipt.  The
instruction it delivers is executable: it names the exact commands the receiver
must run to start, and the exact command that reports completion.  A receiver
that follows it produces evidence without needing another conversation.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

ASSIGNMENT_SCHEMA = "aep-assignment-v1"
#: Phases the supervisor treats as "this task is finished" when written back by
#: the executing agent through ``coord-task-state``.
DONE_PHASES = frozenset({"done", "completed", "complete"})


def event_id_for(workspace_id, task_id):
    """One stable event id per task.

    Re-delivery after a handoff must keep the same id: the workspace watermark
    exists so a late reply can still be attributed to the task it answers.
    """
    seed = f"{workspace_id}/{task_id}".encode("utf-8")
    return "EVT-" + sha256(seed).hexdigest()[:16].upper()


def _aep(coord_root, *parts):
    return " ".join(["aep", "--coord-root", str(coord_root), *parts])


def render(*, agent, task, coord_root, workspace_id, sender, event_id=""):
    """Render the self-executing assignment the receiving agent reads.

    Every line is a command that works as written.  Anything vaguer turns back
    into the problem this module exists to remove.
    """
    requirement = str(task.get("requirement", "")).strip()
    if not requirement:
        raise ValueError("task requirement is required")
    task_id = str(task["id"])
    event_id = event_id or event_id_for(workspace_id, task_id)
    deps = [str(item) for item in (task.get("deps") or [])]
    acceptance = str(task.get("acceptance", "")).strip()
    paths = [str(item) for item in (task.get("paths") or [])]
    lines = [
        "AEP 派工单 / ASSIGNMENT",
        f"workspace: {workspace_id or '-'}",
        f"task: {task_id}",
        f"assignee: {agent}",
        f"sender: {sender}",
        f"requirement: {requirement}",
        f"acceptance: {acceptance or '(见 requirement)'}",
        f"depends_on: {', '.join(deps) if deps else '(none)'}",
        "",
        "这是被指派的任务，不是通知。按顺序执行，每一步都要留下可复验的痕迹：",
        "",
        "1) 先回一行，证明你收到了这一单：",
        f"   AEP-ACK:{event_id}",
        "",
    ]
    if paths:
        lines += ["2) 认领写范围（已被你认领则跳过）：",
                  "   " + _aep(coord_root, "coord-claim", "--agent", agent,
                              "--task", task_id, "--paths", *paths), ""]
    else:
        lines += ["2) 本单未声明写范围；动任何文件前先自行认领。", ""]
    lines += [
        "3) 标记开始：",
        "   " + _aep(coord_root, "coord-task-state", "--agent", agent, "--task", task_id,
                    "--phase", "working", "--goal", _quote(requirement),
                    "--next-action", _quote("先复述你要改什么，再动手")),
        "",
        "4) 执行任务本身。",
        "",
        "5) 完成后写回，`--evidence` 必须是能复算的路径：",
        "   " + _aep(coord_root, "coord-task-state", "--agent", agent, "--task", task_id,
                    "--phase", "done", "--goal", _quote(requirement),
                    "--evidence", "<artifact-path>"),
        "",
        "做不完就写 --phase blocked 并给 --blocked-reason；不要静默停下。",
        f"心跳：每 15 分钟运行一次 {_aep(coord_root, 'coord-claim-renew', '--agent', agent, '--task', task_id, '--ttl-hours', '24')}，",
        "否则租约会过期，任务会被判为失联并转交他人。",
    ]
    return "\n".join(lines)


def _quote(value):
    text = str(value).replace('"', "'")
    return f'"{text}"'


def assign(coord_root, *, workspace_id, task_id, requirement, target,
           paths=(), deps=(), acceptance="", sender="kernel", priority="normal",
           ttl_hours=24.0, allow_launch=False, transport="auto",
           urgent_interject=False, federation_root=None, workspace=None,
           capability="", preferred="", moment=None):
    """Create the task, take the lease, and deposit the order. One call.

    ``federation_root`` is where the workspace DAG lives; ``coord_root`` is
    where claims and orders live.  They are separate on purpose -- the DAG is
    workspace state, the mailbox is transport -- but a caller that keeps both in
    the same directory may pass only ``coord_root``.
    """
    from agent_evolution_protocol.coordination import claim_conflicts, commander
    from agent_evolution_protocol.federation import engine

    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id is required")
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target is required")
    if not isinstance(requirement, str) or not requirement.strip():
        raise ValueError("requirement is required")
    task_id, target = task_id.strip(), target.strip()
    root = Path(federation_root or coord_root)
    evidence = []
    receipt = {"schema": ASSIGNMENT_SCHEMA, "task": task_id, "agent": target,
               "workspace": workspace_id, "evidence": evidence}

    # 1. Existence: the scheduler can only drive work it can see.
    if workspace is None:
        workspace = engine.load(root, workspace_id)
    task = workspace["tasks"].get(task_id)
    if task is None:
        task = engine.add_task(root, workspace, task_id=task_id, requirement=requirement,
                               deps=list(deps), capability=capability, preferred=preferred,
                               moment=moment)
        evidence.append(f"dag:added:{task_id}")
    else:
        evidence.append(f"dag:existing:{task_id}")
        if requirement.strip() and task.get("requirement") != requirement:
            evidence.append("dag:requirement_kept_first_writer_wins")

    # 2. Ownership: a declared scope that nobody holds is not a lock.
    if paths:
        verdict = claim_conflicts.claim(coord_root, agent=target, task=task_id,
                                        paths=list(paths), ttl_hours=float(ttl_hours),
                                        now=moment)
        receipt["claim"] = verdict
        if verdict.get("claimed"):
            evidence.append("claim:created")
        else:
            evidence.append(f"claim:{verdict.get('reason', 'refused')}")
    else:
        receipt["claim"] = {"claimed": False, "reason": "no_scope_declared",
                            "task": task_id, "agent": target}
        evidence.append("claim:no_scope_declared")

    # 3. Acceptance: the order carries the same event id the DAG node does.
    event_id = event_id_for(workspace_id, task_id)
    task.setdefault("paths", list(paths))
    task["event_id"] = event_id
    text = render(agent=target, task={**task, "paths": list(paths) or task.get("paths", [])},
                  coord_root=coord_root, workspace_id=workspace_id, sender=sender,
                  event_id=event_id)
    order = commander.submit_order(
        coord_root, target=target, task=task_id, text=text, sender=sender,
        priority=priority, event_id=event_id, allow_launch=allow_launch,
        transport=transport, workspace_id=workspace_id,
        urgent_interject=urgent_interject)
    evidence.append(f"order:deposited:{order['id']}")
    # Assignment *is* ownership.  Leaving the node ``pending`` after depositing
    # an order makes two sources of truth disagree -- the supervisor sees a
    # task nobody owns while the mailbox already holds a queued assignment --
    # and the disagreement costs a whole heartbeat interval before the first
    # read-back can close anything.
    if task.get("state") in {"pending", ""}:
        task["state"] = "running"
        task["owner"] = target
        task["attempts"] = int(task.get("attempts", 0)) + 1
        engine.EventLog(root, workspace_id).append(
            "task_assigned", actor=sender,
            payload={"task": task_id, "agent": target, "order": order["id"],
                     "event_id": event_id}, moment=moment)
        evidence.append("dag:running")
    engine.save(root, workspace)
    receipt.update({"event_id": event_id, "order": order, "instruction": text,
                    "acceptance": acceptance})
    return receipt


def complete_evidence(coord_root, *, task_id, agent=""):
    """Read back what the executing agent reported, without trusting a screen.

    The supervisor uses this to decide whether a task is finished.  Only an
    explicit phase written by the assignee counts; a message that merely says
    "done" in prose is not evidence.
    """
    import json
    from pathlib import Path as _Path

    folder = _Path(coord_root) / "task_states"
    path = folder / f"{task_id}.json"
    if not path.is_file():
        return {"found": False, "task": task_id}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"found": False, "task": task_id, "error": str(exc)}
    owner = str(payload.get("owner") or payload.get("agent") or "")
    if agent and owner and owner != agent:
        return {"found": True, "task": task_id, "owner": owner, "phase": "",
                "reason": "reported_by_another_agent"}
    phase = str(payload.get("phase", "")).strip().lower()
    return {"found": True, "task": task_id, "owner": owner, "phase": phase,
            "done": phase in DONE_PHASES, "blocked": phase == "blocked",
            "evidence": list(payload.get("evidence") or []),
            "blocked_reason": str(payload.get("blocked_reason", ""))}
