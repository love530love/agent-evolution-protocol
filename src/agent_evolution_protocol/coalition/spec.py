"""Mission coalition specification with fail-closed defaults.

A mission names functional roles (never vendor names) and the agents that may
hold them.  The planner needs three things the rest of the workspace does not
know how to combine: required capabilities, vendor-diversity limits and a
declared write scope for each role.
"""
from __future__ import annotations

from pathlib import Path
import re

from . import io

SCHEMA = "aep-coalition-mission-v1"
SAFE_AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
SAFE_ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
SAFE_CONSTRAINT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
SAFE_PATH = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
CRITICALITIES = ("critical", "normal")

DEFAULT_POLICY = {
    "max_roles_per_vendor": 2,
    "max_critical_roles_per_vendor": 1,
    "allow_single_vendor": False,
    "require_backup": True,
    "backup_depth": 2,
    "allow_degraded": True,
    "allow_vendor_overflow": True,
    "max_primary_roles_per_agent": 2,
}


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label.upper()}_MUST_BE_OBJECT")
    return value


def _text(value, label, *, maximum=500, required=False):
    if value is None:
        if required:
            raise ValueError(f"{label.upper()}_REQUIRED")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label.upper()}_MUST_BE_TEXT")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{label.upper()}_REQUIRED")
    if len(value) > maximum:
        raise ValueError(f"{label.upper()}_TOO_LONG")
    return value


def _integer(value, label, *, minimum, maximum, default=None):
    if value is None:
        if default is None:
            raise ValueError(f"{label.upper()}_REQUIRED")
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label.upper()}_MUST_BE_INTEGER")
    if not minimum <= value <= maximum:
        raise ValueError(f"{label.upper()}_OUT_OF_RANGE")
    return value


def _boolean(value, label, *, default=False):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{label.upper()}_MUST_BE_BOOLEAN")
    return value


def _token(value, label):
    text = _text(value, label, maximum=32, required=True)
    if not SAFE_TOKEN.fullmatch(text):
        raise ValueError(f"INVALID_{label.upper()}")
    return text


def _tokens(value, label, *, allow_empty=False):
    if value is None:
        value = []
    if not isinstance(value, list):
        raise ValueError(f"{label.upper()}_MUST_BE_LIST")
    cleaned = sorted({_token(item, label) for item in value})
    if not cleaned and not allow_empty:
        raise ValueError(f"{label.upper()}_REQUIRED")
    return cleaned


def _constraints(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("CONSTRAINTS_MUST_BE_LIST")
    cleaned = set()
    for item in value:
        text = _text(item, "constraints", maximum=64, required=True)
        if not SAFE_CONSTRAINT.fullmatch(text):
            raise ValueError("INVALID_CONSTRAINTS")
        cleaned.add(text)
    return sorted(cleaned)


def _paths(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("WRITE_SCOPE_MUST_BE_LIST")
    cleaned = []
    for item in value:
        text = _text(item, "write_scope", maximum=240, required=True).replace("\\", "/")
        if text.startswith("/") or not SAFE_PATH.fullmatch(text) or ".." in text.split("/"):
            raise ValueError("INVALID_WRITE_SCOPE")
        cleaned.append(text)
    return sorted(set(cleaned))


def _policy(raw):
    raw = _object(raw or {}, "vendor_policy")
    policy = {
        "max_roles_per_vendor": _integer(
            raw.get("max_roles_per_vendor"), "max_roles_per_vendor",
            minimum=1, maximum=20, default=DEFAULT_POLICY["max_roles_per_vendor"]),
        "max_critical_roles_per_vendor": _integer(
            raw.get("max_critical_roles_per_vendor"), "max_critical_roles_per_vendor",
            minimum=1, maximum=20,
            default=DEFAULT_POLICY["max_critical_roles_per_vendor"]),
        "allow_single_vendor": _boolean(
            raw.get("allow_single_vendor"), "allow_single_vendor",
            default=DEFAULT_POLICY["allow_single_vendor"]),
        "require_backup": _boolean(
            raw.get("require_backup"), "require_backup",
            default=DEFAULT_POLICY["require_backup"]),
        "backup_depth": _integer(
            raw.get("backup_depth"), "backup_depth",
            minimum=0, maximum=5, default=DEFAULT_POLICY["backup_depth"]),
        "allow_degraded": _boolean(
            raw.get("allow_degraded"), "allow_degraded",
            default=DEFAULT_POLICY["allow_degraded"]),
        "allow_vendor_overflow": _boolean(
            raw.get("allow_vendor_overflow"), "allow_vendor_overflow",
            default=DEFAULT_POLICY["allow_vendor_overflow"]),
        "max_primary_roles_per_agent": _integer(
            raw.get("max_primary_roles_per_agent"), "max_primary_roles_per_agent",
            minimum=1, maximum=10,
            default=DEFAULT_POLICY["max_primary_roles_per_agent"]),
    }
    return policy


def _agent(raw, policy, index):
    raw = _object(raw, f"agents[{index}]")
    name = _text(raw.get("agent"), "agent", maximum=64, required=True)
    if not SAFE_AGENT.fullmatch(name):
        raise ValueError("INVALID_AGENT")
    vendor = _token(raw.get("vendor"), "vendor")
    return {
        "agent": name,
        "vendor": vendor,
        "capabilities": _tokens(raw.get("capabilities"), "capabilities", allow_empty=True),
        "max_primary_roles": _integer(
            raw.get("max_primary_roles"), "max_primary_roles",
            minimum=1, maximum=10,
            default=policy["max_primary_roles_per_agent"]),
        "subagent_slots": _integer(
            raw.get("subagent_slots"), "subagent_slots",
            minimum=0, maximum=50, default=2),
    }


def _role(raw, policy, index):
    raw = _object(raw, f"roles[{index}]")
    role_id = _text(raw.get("id"), "role", maximum=32, required=True)
    if not SAFE_ROLE.fullmatch(role_id):
        raise ValueError("INVALID_ROLE")
    criticality = _text(raw.get("criticality", "normal"), "criticality",
                        maximum=16).lower()
    if criticality not in CRITICALITIES:
        raise ValueError("INVALID_CRITICALITY")
    required = _tokens(raw.get("required_capabilities"),
                       "required_capabilities", allow_empty=False)
    acting_raw = raw.get("acting_capabilities")
    acting = (required if acting_raw is None else
              _tokens(acting_raw, "acting_capabilities", allow_empty=False))
    preferred_vendor = _text(raw.get("preferred_vendor", ""), "preferred_vendor",
                             maximum=32)
    if preferred_vendor and not SAFE_TOKEN.fullmatch(preferred_vendor):
        raise ValueError("INVALID_PREFERRED_VENDOR")
    command_role = _text(raw.get("command_role", role_id), "command_role",
                         maximum=32)
    if not SAFE_TOKEN.fullmatch(command_role):
        raise ValueError("INVALID_COMMAND_ROLE")
    constraints = _constraints(raw.get("constraints"))
    return {
        "id": role_id,
        "title": _text(raw.get("title", role_id), "title", maximum=120) or role_id,
        "criticality": criticality,
        "load": _integer(raw.get("load"), "load", minimum=1, maximum=10, default=1),
        "required_capabilities": required,
        "acting_capabilities": acting,
        "preferred_vendor": preferred_vendor,
        "command_role": command_role,
        "allow_scribe_fallback": _boolean(
            raw.get("allow_scribe_fallback"), "allow_scribe_fallback",
            default=policy["allow_degraded"]),
        "write_scope": _paths(raw.get("write_scope")),
        "constraints": constraints,
    }


def load_spec(source):
    """Return a validated, default-filled mission spec.

    ``source`` is either a parsed object or a JSON file path.  Validation is
    intentionally strict: an unknown field is not an error, but a malformed
    value never silently becomes a default.
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.is_file():
            raise ValueError("SPEC_NOT_FOUND")
        try:
            raw = io.read_json(path)
        except (OSError, ValueError) as exc:
            raise ValueError("SPEC_INVALID_JSON") from exc
    elif isinstance(source, dict):
        raw = source
    else:
        raise ValueError("SPEC_MUST_BE_OBJECT_OR_PATH")
    raw = _object(raw, "spec")
    if raw.get("schema") != SCHEMA:
        raise ValueError("UNKNOWN_SPEC_SCHEMA")
    mission = _text(raw.get("mission"), "mission", maximum=64, required=True)
    if not SAFE_ROLE.fullmatch(mission):
        raise ValueError("INVALID_MISSION")
    policy = _policy(raw.get("vendor_policy"))
    agents_raw = raw.get("agents")
    roles_raw = raw.get("roles")
    if not isinstance(agents_raw, list) or not agents_raw:
        raise ValueError("AGENTS_REQUIRED")
    if not isinstance(roles_raw, list) or not roles_raw:
        raise ValueError("ROLES_REQUIRED")
    agents = [_agent(item, policy, index) for index, item in enumerate(agents_raw)]
    roles = [_role(item, policy, index) for index, item in enumerate(roles_raw)]
    names = [item["agent"] for item in agents]
    if len(names) != len(set(names)):
        raise ValueError("DUPLICATE_AGENT")
    role_ids = [item["id"] for item in roles]
    if len(role_ids) != len(set(role_ids)):
        raise ValueError("DUPLICATE_ROLE")
    holders_raw = _object(raw.get("current_holders") or {}, "current_holders")
    holders = {}
    for role_id, name in holders_raw.items():
        if role_id not in role_ids:
            raise ValueError(f"UNKNOWN_HOLDER_ROLE:{role_id}")
        if not isinstance(name, str) or not SAFE_AGENT.fullmatch(name):
            raise ValueError("INVALID_HOLDER_AGENT")
        holders[role_id] = name
    return {
        "schema": SCHEMA,
        "mission": mission,
        "description": _text(raw.get("description", ""), "description",
                              maximum=1000),
        "vendor_policy": policy,
        "agents": sorted(agents, key=lambda item: item["agent"]),
        "roles": roles,
        "current_holders": dict(sorted(holders.items())),
    }


def source_of(source) -> str:
    if isinstance(source, (str, Path)):
        return str(Path(source))
    return "<memory>"
