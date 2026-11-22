"""Vendor-neutral role contract for hands-off-able battle division of labour.

This is pure logic: no filesystem, network, clock or collaboration-area access.
Every output is a JSON-serialisable dict built solely from the arguments, so a
role can be picked up by any agent (including a weak one) without inheriting a
vendor-specific session or an implicit command.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable


SCHEMA_ROLE = "aep-role-contract-role-v1"
SCHEMA_ORDER = "aep-battle-order-v1"
SCHEMA_BRIEF = "aep-role-handoff-brief-v1"

# Mirrored from agent_evolution_protocol/federation/diagnosis.py::QUOTA_SIGNALS.
# Deliberately copied instead of imported so this module stays free of
# cross-package imports and can reason about quotas in isolation.
QUOTA_SIGNALS = frozenset({
    "quota_exhausted", "credit_exhausted", "usage_limit", "time_limit",
})

ROLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# A handoff brief must never carry an absolute path or a shell command.
_PATH_MARKERS = re.compile(r"(?:^[\\/]|[A-Za-z]:[\\/]|\\\\[^\\/])")
_COMMAND_MARKERS = (
    "powershell", "cmd.exe", "bash", "sh -c", "sudo", "rm -", "rm /",
    "del /", "curl ", "wget ", "git push", "git clone", "pip install",
    "npm install", "&&", "||", "|", ">", "<", "`", "$(", "${", ";",
)


def _neutral(value: Any, field: str) -> str:
    """Accept neutral prose only; reject paths and shell-command markers."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    text = value.strip()
    lowered = text.lower()
    if _PATH_MARKERS.search(text) or any(marker in lowered for marker in _COMMAND_MARKERS):
        raise ValueError(f"{field} must be neutral text without paths or shell commands")
    return text


def _names(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    """Normalise a list of lowercase identifiers, preserving declaration order."""
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} must contain non-empty strings")
        token = item.strip()
        if not TOKEN.fullmatch(token):
            raise ValueError(f"invalid {field} token: {token}")
        if token not in result:
            result.append(token)
    if not allow_empty and not result:
        raise ValueError(f"{field} must not be empty")
    return result


def validate_role(role: Any, *, strict: bool = False) -> dict[str, Any]:
    """Validate and normalise a role definition; custom roles are supported."""
    if not isinstance(role, dict):
        raise ValueError("role must be an object")
    required = ("id", "display_name", "responsibility", "required_capability",
                "optional_signals", "handoff_hint")
    missing = [field for field in required if field not in role]
    if missing:
        raise ValueError(f"role missing fields: {', '.join(sorted(missing))}")
    rid = role["id"]
    if not isinstance(rid, str) or not ROLE_ID.fullmatch(rid):
        raise ValueError("invalid role id")
    if strict:
        unknown = sorted(set(role) - set(required))
        if unknown:
            raise ValueError(f"unknown role fields: {', '.join(unknown)}")
    normalised = {
        "id": rid,
        "display_name": _neutral(role["display_name"], "display_name"),
        "responsibility": _neutral(role["responsibility"], "responsibility"),
        "required_capability": _names(role["required_capability"], "required_capability"),
        "optional_signals": _names(role["optional_signals"], "optional_signals",
                                    allow_empty=True),
        "handoff_hint": _neutral(role["handoff_hint"], "handoff_hint"),
    }
    return json.loads(json.dumps(normalised, ensure_ascii=False, allow_nan=False))


ROLES: list[dict[str, Any]] = [
    validate_role({
        "id": "commander",
        "display_name": "调度指挥",
        "responsibility": "统筹赛事攻坚节奏，跟踪各角色进度与阻塞，必要时发起角色交接",
        "required_capability": ["desktop-ui", "cli"],
        "optional_signals": ["quota_exhausted", "time_limit"],
        "handoff_hint": "先确认当前分工表与在途任务，再接管进度跟踪，不要改动他人未交接的工作",
    }),
    validate_role({
        "id": "scout",
        "display_name": "情报侦察",
        "responsibility": "收集并整理赛事规则、资料与公开情报，输出可复核的要点清单",
        "required_capability": ["research", "cli"],
        "optional_signals": ["credit_exhausted", "usage_limit"],
        "handoff_hint": "先读取已有情报清单，补缺不重写，并注明来源与可信度",
    }),
    validate_role({
        "id": "github",
        "display_name": "代码仓库情报",
        "responsibility": "获取公开代码仓库的结构、文档与更新，整理为可引用的仓库情报",
        "required_capability": ["git-access", "cli"],
        "optional_signals": ["usage_limit", "quota_exhausted"],
        "handoff_hint": "先核对已登记的仓库与引用记录，避免重复抓取同一来源",
    }),
    validate_role({
        "id": "builder",
        "display_name": "赛事包构建",
        "responsibility": "制作赛事提交包并回填结果到指定登记表，保留可核对的构建记录",
        "required_capability": ["packaging", "cli"],
        "optional_signals": ["quota_exhausted", "time_limit"],
        "handoff_hint": "先确认赛事包当前版本与回表状态，再续做未完成的打包步骤",
    }),
    validate_role({
        "id": "qa",
        "display_name": "质量验收",
        "responsibility": "对交付物做独立复核，记录证据与未通过项，不代替执行角色修改内容",
        "required_capability": ["review", "cli"],
        "optional_signals": ["time_limit", "credit_warning"],
        "handoff_hint": "先读取验收标准与现有证据，再按项复核并标注缺口",
    }),
    validate_role({
        "id": "scribe",
        "display_name": "文档记录",
        "responsibility": "维护分工与交接记录、要点和结论摘要，保持记录与事实一致",
        "required_capability": ["documentation", "cli"],
        "optional_signals": ["credit_warning", "time_limit"],
        "handoff_hint": "先核对已有记录的时间线与待办，再补记差异",
    }),
]


def _role_index(roles: Iterable[Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for raw in roles:
        role = validate_role(raw)
        if role["id"] in index:
            raise ValueError(f"duplicate role id: {role['id']}")
        index[role["id"]] = role
    return index


def _unassigned(task_id: str, role_id: str, title: str, reason: str,
                display_name: str = "") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "role": role_id,
        "display_name": display_name or role_id,
        "title": title,
        "status": "unassigned",
        "candidate": None,
        "candidates": [],
        "reason": reason,
    }


def plan_battle_order(tasks: Iterable[Any], *, roster: Iterable[Any],
                      quota_signals: Iterable[str],
                      roles: Iterable[Any] | None = None) -> dict[str, Any]:
    """Assign each task a role and one candidate agent, fail-closed.

    Agents whose ``current_signals`` intersect ``quota_signals`` are excluded.
    A role with no qualified candidate stays ``unassigned`` with a reason;
    candidates are suggestions, never an implicit handoff or command.
    """
    if not isinstance(tasks, (list, tuple)):
        raise ValueError("tasks must be a list")
    if not isinstance(roster, (list, tuple)):
        raise ValueError("roster must be a list")
    if isinstance(quota_signals, (str, bytes)):
        raise ValueError("quota_signals must be a collection of names")
    quota = set(quota_signals)
    index = _role_index(list(ROLES) if roles is None else roles)

    prepared_tasks: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("each task must be an object")
        tid = task.get("id")
        if not isinstance(tid, str) or not tid.strip():
            raise ValueError("task id is required")
        tid = tid.strip()
        if tid in seen:
            raise ValueError(f"duplicate task id: {tid}")
        seen.add(tid)
        role_id = task.get("role")
        if not isinstance(role_id, str) or not role_id.strip():
            raise ValueError("task role is required")
        title = _neutral(task["title"], "title") if task.get("title") else ""
        prepared_tasks.append((tid, role_id.strip(), title))

    prepared_roster: list[dict[str, Any]] = []
    for agent in roster:
        if not isinstance(agent, dict):
            raise ValueError("each agent must be an object")
        prepared_roster.append({
            "name": _neutral(agent.get("name"), "agent name"),
            "capabilities": set(_names(agent.get("capabilities", []),
                                       "agent capabilities", allow_empty=True)),
            "signals": set(_names(agent.get("current_signals", []),
                                  "agent current_signals", allow_empty=True)),
        })

    assignments: list[dict[str, Any]] = []
    for tid, role_id, title in sorted(prepared_tasks, key=lambda item: item[0]):
        role = index.get(role_id)
        if role is None:
            assignments.append(_unassigned(tid, role_id, title, "unknown_role"))
            continue
        required = set(role["required_capability"])
        capable = [agent for agent in prepared_roster
                   if required <= agent["capabilities"]]
        if not capable:
            reason = "empty_roster" if not prepared_roster else "no_agent_with_required_capability"
            assignments.append(_unassigned(tid, role_id, title, reason,
                                           role["display_name"]))
            continue
        eligible = [agent for agent in capable if not (agent["signals"] & quota)]
        if not eligible:
            assignments.append(_unassigned(tid, role_id, title,
                                           "all_capable_agents_quota_limited",
                                           role["display_name"]))
            continue
        eligible.sort(key=lambda agent: agent["name"])
        assignments.append({
            "task_id": tid,
            "role": role_id,
            "display_name": role["display_name"],
            "title": title,
            "status": "assigned",
            "candidate": eligible[0]["name"],
            "candidates": [agent["name"] for agent in eligible],
            "reason": "",
        })

    assigned = sum(1 for item in assignments if item["status"] == "assigned")
    result = {
        "schema": SCHEMA_ORDER,
        "assignments": assignments,
        "summary": {
            "total": len(assignments),
            "assigned": assigned,
            "unassigned": len(assignments) - assigned,
        },
        "note": "Candidates are suggestions only; a handoff is valid after the receiver acknowledges.",
    }
    return json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))


def handoff_brief(role: Any, *, from_agent: str, to_agent: str, reason: str,
                  roles: Iterable[Any] | None = None) -> dict[str, Any]:
    """Build a structured, vendor-neutral brief for taking over a role."""
    index = _role_index(list(ROLES) if roles is None else roles)
    if isinstance(role, str):
        resolved = index.get(role.strip())
        if resolved is None:
            raise ValueError(f"unknown role: {role}")
    else:
        resolved = validate_role(role)
    sender = _neutral(from_agent, "from_agent")
    receiver = _neutral(to_agent, "to_agent")
    cause = _neutral(reason, "reason")

    brief = {
        "schema": SCHEMA_BRIEF,
        "role": resolved["id"],
        "display_name": resolved["display_name"],
        "responsibility": resolved["responsibility"],
        "required_capability": list(resolved["required_capability"]),
        "from_agent": sender,
        "to_agent": receiver,
        "reason": cause,
        "current_state_hint": (
            f"角色 {resolved['display_name']} 由 {sender} 交接给 {receiver}，原因：{cause}。"
            "接手方应先核对当前在途任务与已登记证据，再继续推进。"
        ),
        "first_step": (
            f"以 {resolved['display_name']} 身份确认分工范围、现有产物状态与未完成项，"
            "再开始接手后的第一步操作。"
        ),
        "handoff_hint": resolved["handoff_hint"],
        "evidence_required": [
            "handoff_reason_recorded",
            "role_scope_confirmed",
            "existing_artifacts_reviewed",
            "receiver_acknowledged",
        ],
        "note": "本简报仅用于协调分工，不构成授权、命令或执行凭据。",
    }
    return json.loads(json.dumps(brief, ensure_ascii=False, allow_nan=False))


__all__ = [
    "QUOTA_SIGNALS", "ROLES", "SCHEMA_ROLE", "SCHEMA_ORDER", "SCHEMA_BRIEF",
    "validate_role", "plan_battle_order", "handoff_brief",
]
