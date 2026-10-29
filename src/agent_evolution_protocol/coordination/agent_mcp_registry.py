"""Per-agent MCP bundles and session-aware dispatch planning.

An MCP server is a capability provider, not an Agent session.  This registry
keeps those concerns separate: each Agent gets a declarative bundle of MCP
servers, launch hints, session locators, and dispatch fallbacks.  Registration,
config rendering, and planning are deliberately non-executing.  A real send
still goes through the existing wake/session adapters and must produce a
message-visible receipt and an Agent ACK.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any

from .workspace_identity import validate_workspace_id


NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
CAPABILITIES = {
    "launch_program", "locate_session", "wake", "dispatch", "readback",
    "browser", "filesystem", "rea", "mcp_probe", "screenshot",
}
OPERATIONS = {"launch", "locate", "wake", "dispatch", "readback", "browser", "rea", "mcp_probe"}
CHANNELS = {"native_api", "aep_mcp", "desktop_mcp", "uia", "cdp", "clawchat", "mailbox"}
PROFILE_FIELDS = {
    "agent", "display_name", "client", "workspace_id", "servers", "capabilities",
    "locator", "launch", "fallbacks", "priority", "metadata", "acceptance",
    "registered_at",
}
SERVER_FIELDS = {"name", "command", "args", "cwd", "env", "enabled", "provider", "capabilities"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_name(value: Any, field: str) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase slug")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a string list")
    return list(value)


def _server(server: Any) -> dict[str, Any]:
    if not isinstance(server, dict) or set(server) - SERVER_FIELDS:
        raise ValueError("invalid MCP server fields")
    name = _valid_name(server.get("name"), "server.name")
    command = server.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("server.command is required")
    args = _string_list(server.get("args", []), "server.args")
    cwd = server.get("cwd", "")
    if cwd and (not isinstance(cwd, str) or not Path(cwd).is_absolute()):
        raise ValueError("server.cwd must be an absolute path when provided")
    env = server.get("env", {})
    if not isinstance(env, dict) or any(
        not isinstance(k, str) or not k or "=" in k or "\0" in k or
        not isinstance(v, str) or "\0" in v for k, v in env.items()
    ):
        raise ValueError("server.env must be a string map")
    capabilities = _string_list(server.get("capabilities", []), "server.capabilities")
    unknown = set(capabilities) - CAPABILITIES
    if unknown:
        raise ValueError(f"unknown server capabilities: {sorted(unknown)}")
    return {
        "name": name, "command": command.strip(), "args": args, "cwd": cwd,
        "env": dict(env), "enabled": bool(server.get("enabled", True)),
        "provider": str(server.get("provider", "generic-mcp")),
        "capabilities": capabilities,
    }


def validate_profile(profile: Any) -> dict[str, Any]:
    """Validate and normalize a per-Agent MCP profile without executing it."""
    if not isinstance(profile, dict) or set(profile) - PROFILE_FIELDS:
        raise ValueError("invalid Agent MCP profile fields")
    agent = _valid_name(profile.get("agent"), "agent")
    client = profile.get("client", {})
    if not isinstance(client, dict):
        raise ValueError("client must be an object")
    for key in ("kind", "executable", "session_id", "process", "window_title"):
        if key in client and not isinstance(client[key], str):
            raise ValueError(f"client.{key} must be a string")
    client = dict(client)
    client["kind"] = client.get("kind", "desktop")
    if client["kind"] not in {"desktop", "browser", "cli", "api", "clawchat"}:
        raise ValueError("client.kind must be desktop/browser/cli/api/clawchat")
    client["args"] = _string_list(client.get("args", []), "client.args")
    workspace_id = profile.get("workspace_id", "")
    if workspace_id:
        validate_workspace_id(workspace_id)
    servers = profile.get("servers", [])
    if not isinstance(servers, list) or len(servers) > 32:
        raise ValueError("servers must be a list with at most 32 entries")
    normalized_servers = [_server(item) for item in servers]
    names = [item["name"] for item in normalized_servers]
    if len(names) != len(set(names)):
        raise ValueError("MCP server names must be unique per Agent")
    capabilities = _string_list(profile.get("capabilities", []), "capabilities")
    unknown = set(capabilities) - CAPABILITIES
    if unknown:
        raise ValueError(f"unknown capabilities: {sorted(unknown)}")
    locator = profile.get("locator", {})
    if not isinstance(locator, dict):
        raise ValueError("locator must be an object")
    locator = dict(locator)
    locator["transports"] = _string_list(locator.get("transports", []), "locator.transports")
    locator["window_titles"] = _string_list(locator.get("window_titles", []), "locator.window_titles")
    launcher = profile.get("launch", {})
    if not isinstance(launcher, dict):
        raise ValueError("launch must be an object")
    launcher = dict(launcher)
    if "executable" in launcher and not isinstance(launcher["executable"], str):
        raise ValueError("launch.executable must be a string")
    launcher["args"] = _string_list(launcher.get("args", []), "launch.args")
    fallbacks = _string_list(profile.get("fallbacks", []), "fallbacks")
    if not fallbacks or any(item not in CHANNELS for item in fallbacks):
        raise ValueError("fallbacks must contain known dispatch channels")
    priority = profile.get("priority", 100)
    if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 1000:
        raise ValueError("priority must be an integer between 0 and 1000")
    return {
        "agent": agent,
        "display_name": str(profile.get("display_name", agent)),
        "client": client,
        "workspace_id": workspace_id,
        "servers": normalized_servers,
        "capabilities": capabilities,
        "locator": locator,
        "launch": launcher,
        "fallbacks": fallbacks,
        "priority": priority,
        "metadata": dict(profile.get("metadata", {})) if isinstance(profile.get("metadata", {}), dict) else {},
        "acceptance": dict(profile.get("acceptance", {})) if isinstance(profile.get("acceptance", {}), dict) else {},
    }


def _profile_path(root: Path, agent: str) -> Path:
    return Path(root) / "clients" / "agent_mcp" / f"{_valid_name(agent, 'agent')}.json"


def register(root: Path, profile: dict[str, Any], *, replace: bool = True) -> dict[str, Any]:
    normalized = validate_profile(profile)
    path = _profile_path(root, normalized["agent"])
    if path.exists() and not replace:
        raise ValueError(f"Agent MCP profile already exists: {normalized['agent']}")
    normalized["registered_at"] = _now()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return {"agent": normalized["agent"], "registered": True, "path": str(path),
            "execution_started": False, "note": "Profile registration is not an MCP probe or Agent ACK."}


def load(root: Path, agent: str) -> dict[str, Any]:
    path = _profile_path(root, agent)
    if not path.is_file():
        raise ValueError(f"Agent MCP profile not found: {agent}")
    return validate_profile(json.loads(path.read_text(encoding="utf-8")))


def inventory(root: Path) -> dict[str, Any]:
    folder = Path(root) / "clients" / "agent_mcp"
    rows: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            profile = validate_profile(json.loads(path.read_text(encoding="utf-8")))
            missing = []
            for server in profile["servers"]:
                if Path(server["command"]).is_absolute() and not Path(server["command"]).exists():
                    missing.append(server["command"])
                if server.get("cwd") and not Path(server["cwd"]).is_dir():
                    missing.append(server["cwd"])
            rows.append({"agent": profile["agent"], "status": "DEPENDENCY_MISSING" if missing else "REGISTERED",
                         "missing": missing, "server_count": len(profile["servers"]),
                         "capabilities": profile["capabilities"], "fallbacks": profile["fallbacks"]})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            rows.append({"agent": path.stem, "status": "INVALID", "error": str(exc)})
    return {"profiles": rows, "count": len(rows), "delivery_verified": False}


def render_config(root: Path, agent: str, workspace_id: str, *, coord_root: str = "") -> dict[str, Any]:
    """Render a client-specific MCP config; never starts a server or client."""
    validate_workspace_id(workspace_id)
    profile = load(root, agent)
    if profile["workspace_id"] and profile["workspace_id"] != workspace_id:
        raise ValueError("WORKSPACE_MISMATCH")
    servers: dict[str, dict[str, Any]] = {}
    for server in profile["servers"]:
        if not server["enabled"]:
            continue
        row: dict[str, Any] = {"command": server["command"], "args": server["args"]}
        if server.get("cwd"):
            row["cwd"] = server["cwd"]
        env = dict(server.get("env", {}))
        env.update({"AEP_WORKSPACE_ID": workspace_id, "AEP_AGENT_ID": profile["agent"]})
        if coord_root:
            env["AEP_COORD_ROOT"] = coord_root
        row["env"] = env
        servers[server["name"]] = row
    return {"agent": profile["agent"], "workspace_id": workspace_id,
            "mcpServers": servers, "execution_started": False,
            "note": "Import this config into the Agent client, then bind the reported session and run an ACK acceptance."}


def plan_dispatch(root: Path, agent: str, workspace_id: str, operation: str, *, session_id: str = "") -> dict[str, Any]:
    """Build a deterministic channel plan while refusing ambiguous sessions."""
    validate_workspace_id(workspace_id)
    if operation not in OPERATIONS:
        raise ValueError("unknown operation")
    profile = load(root, agent)
    if profile["workspace_id"] and profile["workspace_id"] != workspace_id:
        raise ValueError("WORKSPACE_MISMATCH")
    bound = session_id or profile["client"].get("session_id", "") or profile["locator"].get("session_id", "")
    if not bound and operation in {"wake", "dispatch", "readback"}:
        return {"status": "SESSION_BINDING_REQUIRED", "agent": agent, "workspace_id": workspace_id,
                "operation": operation, "channels": [], "execution_started": False,
                "next": "discover a unique original session and anchor its session_id before input"}
    channels = list(profile["fallbacks"])
    if operation == "rea" and "rea" not in profile["capabilities"]:
        return {"status": "NO_CAPABILITY", "agent": agent, "workspace_id": workspace_id,
                "operation": operation, "channels": [], "execution_started": False}
    return {"status": "READY_TO_DISPATCH", "agent": agent, "workspace_id": workspace_id,
            "operation": operation, "session_id": bound, "channels": channels,
            "server_candidates": [s["name"] for s in profile["servers"] if s["enabled"]],
            "execution_started": False,
            "next": "acquire the workspace/session lease, execute one channel, read back, then require same-event ACK"}
