import json
import re

import pytest

from agent_evolution_protocol.coordination import role_contract as rc


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _contest_roster():
    return [
        {"name": "workbuddy", "capabilities": ["desktop-ui", "cli"], "current_signals": []},
        {"name": "codex", "capabilities": ["research", "cli"], "current_signals": []},
        {"name": "copilot", "capabilities": ["git-access", "cli"], "current_signals": []},
        {"name": "qoder", "capabilities": ["packaging", "cli"], "current_signals": []},
    ]


def _contest_tasks():
    return [
        {"id": "T-4", "role": "builder", "title": "制作赛事包"},
        {"id": "T-1", "role": "commander", "title": "跟踪进度"},
        {"id": "T-3", "role": "github", "title": "收集仓库情报"},
        {"id": "T-2", "role": "scout", "title": "整理赛事资料"},
    ]


def test_validate_role_accepts_predefined_roles_and_is_json_serialisable():
    assert len(rc.ROLES) == 6
    for role in rc.ROLES:
        validated = rc.validate_role(role, strict=True)
        assert validated["id"]
        assert validated["required_capability"]
        assert json.loads(json.dumps(validated, ensure_ascii=False)) == validated


def test_validate_role_rejects_invalid_definitions():
    valid = rc.ROLES[0]
    with pytest.raises(ValueError, match="role must be an object"):
        rc.validate_role("commander")
    with pytest.raises(ValueError, match="invalid role id"):
        rc.validate_role({**valid, "id": "Bad Id"})
    with pytest.raises(ValueError, match="must not be empty"):
        rc.validate_role({**valid, "required_capability": []})
    with pytest.raises(ValueError, match="missing fields"):
        rc.validate_role({"id": "x"})
    with pytest.raises(ValueError, match="unknown role fields"):
        rc.validate_role({**valid, "extra": 1}, strict=True)


def test_four_contest_roles_are_present():
    by_id = {role["id"]: role for role in rc.ROLES}
    for role_id in ("commander", "scout", "github", "builder"):
        assert role_id in by_id
    assert "desktop-ui" in by_id["commander"]["required_capability"]
    assert "research" in by_id["scout"]["required_capability"]
    assert "git-access" in by_id["github"]["required_capability"]
    assert "packaging" in by_id["builder"]["required_capability"]
    assert all(by_id[role_id]["handoff_hint"] for role_id in
               ("commander", "scout", "github", "builder"))


def test_plan_battle_order_assigns_contest_roles():
    plan = rc.plan_battle_order(
        _contest_tasks(), roster=_contest_roster(), quota_signals=rc.QUOTA_SIGNALS,
    )
    assert plan["schema"] == rc.SCHEMA_ORDER
    assert plan["summary"] == {"total": 4, "assigned": 4, "unassigned": 0}
    by_task = {item["task_id"]: item for item in plan["assignments"]}
    assert list(by_task) == ["T-1", "T-2", "T-3", "T-4"]
    assert by_task["T-1"]["candidate"] == "workbuddy"
    assert by_task["T-2"]["candidate"] == "codex"
    assert by_task["T-3"]["candidate"] == "copilot"
    assert by_task["T-4"]["candidate"] == "qoder"
    assert all(item["status"] == "assigned" for item in plan["assignments"])


def test_quota_marked_agent_is_skipped():
    roster = [
        {"name": "codex", "capabilities": ["research", "cli"],
         "current_signals": ["quota_exhausted"]},
        {"name": "backup", "capabilities": ["research", "cli"], "current_signals": []},
    ]
    plan = rc.plan_battle_order(
        [{"id": "T-1", "role": "scout"}], roster=roster,
        quota_signals=rc.QUOTA_SIGNALS,
    )
    assignment = plan["assignments"][0]
    assert assignment["status"] == "assigned"
    assert assignment["candidate"] == "backup"
    assert "codex" not in assignment["candidates"]

    blocked = rc.plan_battle_order(
        [{"id": "T-1", "role": "scout"}], roster=roster[:1],
        quota_signals=rc.QUOTA_SIGNALS,
    )
    assert blocked["assignments"][0]["status"] == "unassigned"
    assert blocked["assignments"][0]["reason"] == "all_capable_agents_quota_limited"
    assert blocked["assignments"][0]["candidate"] is None


def test_no_qualified_candidate_is_marked_unassigned():
    weak = [{"name": "weak", "capabilities": ["cli"], "current_signals": []}]
    plan = rc.plan_battle_order(
        [{"id": "T-1", "role": "scout"}], roster=weak,
        quota_signals=rc.QUOTA_SIGNALS,
    )
    assert plan["assignments"][0]["reason"] == "no_agent_with_required_capability"
    assert plan["summary"]["unassigned"] == 1

    empty = rc.plan_battle_order(
        [{"id": "T-1", "role": "scout"}], roster=[],
        quota_signals=rc.QUOTA_SIGNALS,
    )
    assert empty["assignments"][0]["reason"] == "empty_roster"
    unknown = rc.plan_battle_order(
        [{"id": "T-1", "role": "ghost"}], roster=_contest_roster(),
        quota_signals=rc.QUOTA_SIGNALS,
    )
    assert unknown["assignments"][0]["reason"] == "unknown_role"


def test_handoff_brief_structure_is_complete():
    brief = rc.handoff_brief(
        "scout", from_agent="codex", to_agent="qoder", reason="quota_exhausted",
    )
    assert brief["schema"] == rc.SCHEMA_BRIEF
    for key in ("role", "display_name", "responsibility", "required_capability",
                "from_agent", "to_agent", "reason", "current_state_hint",
                "first_step", "handoff_hint", "evidence_required", "note"):
        assert key in brief
    role = next(role for role in rc.ROLES if role["id"] == "scout")
    assert brief["responsibility"] == role["responsibility"]
    assert brief["handoff_hint"] == role["handoff_hint"]
    assert brief["from_agent"] == "codex" and brief["to_agent"] == "qoder"
    assert len(brief["evidence_required"]) >= 3
    assert brief["current_state_hint"] and brief["first_step"]
    assert json.loads(json.dumps(brief, ensure_ascii=False)) == brief


def test_handoff_brief_contains_no_command_or_absolute_path():
    brief = rc.handoff_brief(
        "commander", from_agent="workbuddy", to_agent="weak-agent",
        reason="quota_exhausted",
    )
    markers = ("powershell", "cmd.exe", "bash", "sudo", "rm ", "del ", "curl ",
               "wget ", "git ", "&&", "||", "|", ">", "<", "`", "$(")
    for text in _strings(brief):
        lowered = text.lower()
        assert not re.search(r"[A-Za-z]:[\\/]", text)
        assert not text.startswith(("/", "\\"))
        assert not any(marker in lowered for marker in markers)


def test_handoff_brief_rejects_unsafe_reason():
    with pytest.raises(ValueError, match="neutral text"):
        rc.handoff_brief("scout", from_agent="codex", to_agent="qoder",
                         reason="见 K:/tmp/notes.txt")
    with pytest.raises(ValueError, match="neutral text"):
        rc.handoff_brief("scout", from_agent="codex", to_agent="qoder",
                         reason="rm -rf build")
    with pytest.raises(ValueError, match="unknown role"):
        rc.handoff_brief("ghost", from_agent="codex", to_agent="qoder",
                         reason="quota_exhausted")


def test_plan_is_deterministic_across_input_order():
    first = rc.plan_battle_order(
        _contest_tasks(), roster=_contest_roster(), quota_signals=rc.QUOTA_SIGNALS,
    )
    second = rc.plan_battle_order(
        list(reversed(_contest_tasks())),
        roster=list(reversed(_contest_roster())),
        quota_signals=frozenset(sorted(rc.QUOTA_SIGNALS)),
    )
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_custom_role_is_supported_by_validate_plan_and_brief():
    custom = {
        "id": "translator",
        "display_name": "翻译校订",
        "responsibility": "校对赛事材料的双语表述并标注术语差异",
        "required_capability": ["cli"],
        "optional_signals": [],
        "handoff_hint": "先核对术语表再续校",
    }
    validated = rc.validate_role(custom, strict=True)
    extended = [*rc.ROLES, validated]
    plan = rc.plan_battle_order(
        [{"id": "T-9", "role": "translator"}],
        roster=[{"name": "weak", "capabilities": ["cli"], "current_signals": []}],
        quota_signals=rc.QUOTA_SIGNALS, roles=extended,
    )
    assert plan["assignments"][0]["candidate"] == "weak"
    brief = rc.handoff_brief(
        validated, from_agent="codex", to_agent="weak",
        reason="quota_exhausted", roles=extended,
    )
    assert brief["role"] == "translator"
    assert brief["responsibility"] == validated["responsibility"]
