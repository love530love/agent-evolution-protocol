"""Discover optional desktop MCP providers without injecting desktop input."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
OPERATIONS = {"windows", "snapshot", "read", "locate", "type", "click", "keys", "select", "screenshot", "clipboard", "wait"}
CATALOG = {
    "sbroenne": {"project": "sbroenne/mcp-windows", "runtime": "dotnet", "priority": 10},
    "shuyu": {"project": "shuyu-labs/Windows-MCP.Net", "runtime": "dotnet", "priority": 20},
    "mcpcontrol": {"project": "claude-did-this/MCPControl", "runtime": "node", "priority": 30},
    "mario": {"project": "mcp-windows-desktop-automation", "runtime": "node+autoit", "priority": 40},
    "cursortouch": {"project": "CursorTouch/Windows-MCP", "runtime": "python/uv", "priority": 50},
}


def _name(value: str) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value) or re.fullmatch(r"con|prn|aux|nul|com[1-9]|lpt[1-9]", value):
        raise ValueError("invalid desktop provider name")
    return value


def _path(root: Path, name: str) -> Path:
    return Path(root) / "desktop_providers" / f"{_name(name)}.json"


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _digest(profile: dict) -> str:
    return hashlib.sha256(json.dumps(profile, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def register(root: Path, profile: dict) -> dict:
    if not isinstance(profile, dict) or set(profile) - {"name", "command", "args", "cwd", "sdk_python", "required_paths", "bindings", "priority", "env"}:
        raise ValueError("unknown or invalid desktop provider fields")
    name = _name(profile.get("name"))
    for field in ("command", "sdk_python"):
        value = profile.get(field)
        if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).is_file():
            raise ValueError(f"{field} must be an existing absolute executable path")
    if Path(profile["command"]).suffix.lower() in {".cmd", ".bat", ".ps1"}:
        raise ValueError("use an executable directly, such as node.exe, dotnet.exe or uv.exe")
    for field in ("args", "required_paths"):
        if not isinstance(profile.get(field, []), list) or any(not isinstance(x, str) for x in profile.get(field, [])):
            raise ValueError(f"{field} must be a string list")
    cwd = profile.get("cwd")
    if cwd and (not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir()):
        raise ValueError("cwd must be an existing absolute directory")
    for dependency in profile.get("required_paths", []):
        if not Path(dependency).is_absolute():
            raise ValueError("required_paths must be absolute")
    bindings = profile.get("bindings", {})
    environment = profile.get("env", {})
    if not isinstance(environment, dict) or any(
        not isinstance(key, str) or not key or "=" in key or "\0" in key or
        not isinstance(value, str) or "\0" in value for key, value in environment.items()
    ):
        raise ValueError("env must contain valid string environment keys and values")
    if not isinstance(bindings, dict) or set(bindings) - OPERATIONS or any(not isinstance(v, str) or not v.strip() for v in bindings.values()):
        raise ValueError("bindings must map known operations to exact MCP tool names")
    priority = profile.get("priority", CATALOG.get(name, {}).get("priority", 100))
    if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 1000:
        raise ValueError("priority must be an integer between 0 and 1000")
    row = {**profile, "priority": priority}
    _write(_path(root, name), row)
    return {"name": name, "registered": True, "probed": False, "note": "Registration is not an execution or delivery test."}


def inventory(root: Path) -> dict:
    folder = Path(root) / "desktop_providers"
    names = set(CATALOG) | {p.stem for p in folder.glob("*.json")}
    rows = []
    for name in sorted(names):
        path = _path(root, name)
        profile = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        missing = [p for p in (profile or {}).get("required_paths", []) if not Path(p).exists()]
        if profile:
            missing += [profile[k] for k in ("command", "sdk_python") if not Path(profile[k]).is_file()]
        receipt_path = folder / "receipts" / f"{name}.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else {}
        matching = bool(profile and receipt.get("profile_sha256") == _digest(profile))
        rows.append({"name": name, **CATALOG.get(name, {}), "registered": bool(profile),
                     "missing_dependencies": missing, "status": "UNREGISTERED" if not profile else (
                         "DEPENDENCY_MISSING" if missing else receipt.get("status", "UNPROBED") if matching else "UNPROBED"),
                     "profile": profile, "receipt": receipt if matching else {}})
    return {"providers": rows, "delivery_verified": False}


def probe(root: Path, name: str, *, timeout: int = 30) -> dict:
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 60:
        raise ValueError("timeout must be 1..60 seconds")
    path = _path(root, name)
    profile = json.loads(path.read_text(encoding="utf-8"))
    row = next(r for r in inventory(root)["providers"] if r["name"] == name)
    if row["missing_dependencies"]:
        raise ValueError("missing dependencies: " + ", ".join(row["missing_dependencies"]))
    result = {"name": name, "at": datetime.now(timezone.utc).isoformat(),
              "profile_sha256": _digest(profile), "status": "PROBE_FAILED", "tools": [],
              "desktop_input_performed": False, "delivery_verified": False}
    try:
        run = subprocess.run([profile["sdk_python"], str(Path(__file__).with_name("desktop_mcp_worker.py")),
                              "--profile", str(path.resolve()), "--timeout", str(timeout)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=timeout + 10, check=False,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if run.returncode:
            try:
                failure = json.loads(run.stdout)
            except (ValueError, TypeError):
                failure = {}
            if not isinstance(failure, dict):
                failure = {}
            result["error"] = failure.get("error") or run.stderr[:1500] + run.stderr[-1500:] or "MCP discovery failed"
            if failure.get("server_stderr"):
                result["server_stderr"] = str(failure["server_stderr"])[:4000]
        else:
            data = json.loads(run.stdout)
            if not isinstance(data.get("tools"), list) or not all(isinstance(t, dict) and isinstance(t.get("name"), str) for t in data["tools"]):
                raise ValueError("invalid MCP tool discovery result")
            result.update(status="TOOLS_DISCOVERED", tools=data["tools"])
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result["error"] = str(exc)
    _write(Path(root) / "desktop_providers" / "receipts" / f"{name}.json", result)
    return {**result, "tools": [tool["name"] for tool in result["tools"]]}


def plan(root: Path, *, operation: str, previous_state: str = "NOT_STARTED") -> dict:
    if operation not in OPERATIONS:
        raise ValueError("unknown desktop operation")
    if previous_state not in {"NOT_STARTED", "PROVEN_NOT_SENT"}:
        return {"status": "VERIFY_PREVIOUS_ATTEMPT", "candidates": [], "execution_started": False}
    candidates = []
    for row in inventory(root)["providers"]:
        if row["status"] != "TOOLS_DISCOVERED":
            continue
        receipt = row["receipt"]
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(receipt["at"])).total_seconds()
        except (KeyError, ValueError, TypeError):
            continue
        if not 0 <= age <= 1800:
            continue
        tool_name = row["profile"].get("bindings", {}).get(operation)
        tool = next((t for t in receipt["tools"] if t["name"] == tool_name), None)
        if tool:
            candidates.append({"provider": row["name"], "tool": tool_name,
                               "input_schema": tool.get("inputSchema", {}), "priority": row["profile"]["priority"]})
    return {"status": "PLAN_ONLY" if candidates else "NO_QUALIFIED_PROVIDER",
            "operation": operation, "candidates": sorted(candidates, key=lambda r: (r["priority"], r["provider"])),
            "execution_started": False, "note": "MCP tool discovery does not prove original-session targeting or safe retry. Use a verified session adapter and the shared desktop-input lease."}


def export_config(root: Path, name: str) -> dict:
    """Return one explicit opt-in stdio client configuration; never edit clients."""
    name = _name(name)
    row = next((r for r in inventory(root)["providers"] if r["name"] == name), None)
    if not row or not row["registered"] or row["missing_dependencies"]:
        raise ValueError("provider must be registered with available dependencies")
    profile = row["profile"]
    # cwd is not portable across MCP clients: refuse to silently discard it.
    if profile.get("cwd"):
        raise ValueError("provider requires cwd; use a client-specific launcher supporting cwd")
    if profile.get("env"):
        raise ValueError("environment may contain secrets; export requires a secret-free launcher")
    return {"mcpServers": {name: {"command": profile["command"], "args": profile.get("args", [])}}}
