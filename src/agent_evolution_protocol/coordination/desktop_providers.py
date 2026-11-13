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

from agent_evolution_protocol.coordination import client_registry

NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
OPERATIONS = {"windows", "snapshot", "read", "locate", "type", "click", "keys", "select", "screenshot", "clipboard", "wait"}
CATALOG = {
    "cua_driver": {"project": "trycua/cua", "runtime": "python/rust+mcp", "priority": 5},
    "sbroenne": {"project": "sbroenne/mcp-windows", "runtime": "dotnet", "priority": 10},
    "shuyu": {"project": "shuyu-labs/Windows-MCP.Net", "runtime": "dotnet", "priority": 20},
    "mcpcontrol": {"project": "claude-did-this/MCPControl", "runtime": "node", "priority": 30},
    "mario": {"project": "mcp-windows-desktop-automation", "runtime": "node+autoit", "priority": 40},
    "cursortouch": {"project": "CursorTouch/Windows-MCP", "runtime": "python/uv", "priority": 50},
}

# A provider being installed tells us almost nothing about whether it can see
# the *specific* original conversation that a commander wants to use.  Keep
# these vocabulary values intentionally small and evidence-oriented so the
# scheduler never treats a window enumeration as a delivered message.
CAPABILITY_STATES = {
    "TREE_USABLE", "TREE_PARTIAL", "TREE_OPAQUE", "TREE_TIMEOUT", "TREE_FAILED",
    "WINDOW_NOT_FOUND", "WINDOW_AMBIGUOUS", "WINDOW_NOT_BOUND",
    "OUT_OF_SCOPE", "PROVIDER_UNREADY", "SESSION_UNATTESTED",
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


def _capability_path(root: Path, provider: str, agent: str) -> Path:
    """Return the durable, per-client read-only probe receipt path.

    ``provider`` and ``agent`` are independently validated: joining them in a
    filename without that check would let a roster value escape the evidence
    directory.  Receipts deliberately contain no accessibility tree or chat
    text, only the minimum metadata needed to decide whether a route merits
    further calibration.
    """
    return Path(root) / "desktop_providers" / "capabilities" / f"{_name(provider)}--{_name(agent)}.json"


def _short_error(value: object, *, limit: int = 1000) -> str:
    """Retain a diagnostic without persisting an arbitrary UI transcript."""
    return str(value or "unknown provider error").replace("\x00", "")[:limit]


def _run_json(argv: list[str], *, timeout: int) -> dict:
    """Run one read-only upstream command and parse its JSON envelope.

    A command failure is represented in the receipt rather than raised.  The
    calling probe must be able to distinguish a timed-out accessibility tree
    from an unavailable executable, and neither case is a message delivery.
    """
    try:
        run = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout + 5, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": {"code": "PROCESS_TIMEOUT", "message": "provider process timed out"}}
    except OSError as exc:
        return {"ok": False, "error": {"code": "PROCESS_ERROR", "message": _short_error(exc)}}
    try:
        payload = json.loads(run.stdout)
    except (TypeError, ValueError):
        return {"ok": False, "error": {"code": "INVALID_JSON", "message": _short_error(run.stderr or run.stdout)}}
    if not isinstance(payload, dict):
        return {"ok": False, "error": {"code": "INVALID_JSON", "message": "provider JSON root is not an object"}}
    if run.returncode and payload.get("ok") is True:
        return {"ok": False, "error": {"code": "PROCESS_EXIT", "message": f"provider exited {run.returncode}"}}
    return payload


def _snapshot_summary(payload: dict, *, forced_electron_a11y: bool) -> dict:
    """Normalise agent-desktop snapshot output without retaining its UI tree."""
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if payload.get("ok") is True:
        refs = data.get("ref_count")
        controls = _tree_control_summary(data.get("tree"))
        if isinstance(refs, int) and refs > 0 and controls["composer_input_count"] > 0:
            # This proves only that the accessibility surface exposes an input
            # candidate.  It cannot prove the original chat, that an Enter key
            # sends, or that the eventual message becomes visible.
            status = "TREE_USABLE"
        elif isinstance(refs, int) and refs > 0:
            status = "TREE_PARTIAL"
        elif refs == 0:
            status = "TREE_OPAQUE"
        else:
            status = "TREE_FAILED"
        window = data.get("window") if isinstance(data.get("window"), dict) else {}
        return {
            "route": "agent_desktop.uia",
            "forced_electron_a11y": forced_electron_a11y,
            "status": status,
            "ref_count": refs if isinstance(refs, int) else None,
            "snapshot_id": str(data.get("snapshot_id", ""))[:128],
            "window_id": str(window.get("id", ""))[:128],
            **controls,
        }
    code = str(error.get("code", "") or "PROVIDER_ERROR")
    message = _short_error(error.get("message", ""))
    text = f"{code} {message}".lower()
    status = "TREE_TIMEOUT" if "timeout" in text or "timed out" in text else "TREE_FAILED"
    return {
        "route": "agent_desktop.uia",
        "forced_electron_a11y": forced_electron_a11y,
        "status": status,
        "error_code": code[:128],
        "error": message,
    }


def _tree_control_summary(tree: object) -> dict:
    """Count actionable composer signals without retaining UI text or the tree.

    Electron clients sometimes expose only a title bar/group with a writable
    accessibility value.  A nonzero ref count is therefore not enough to
    permit a message action.  This intentionally conservative classifier
    accepts only editor-like roles (or a non-titlebar editable ref) as a
    *candidate* composer; the driver still needs session attestation and
    MESSAGE_VISIBLE/ACK proof before it can claim delivery.
    """
    stack = [tree] if isinstance(tree, dict) else []
    nodes = 0
    refs = 0
    composer_inputs = 0
    send_controls = 0
    input_roles = {"textbox", "text box", "edit", "editor", "document", "textarea", "text area", "searchbox", "search box"}
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        nodes += 1
        children = node.get("children")
        if isinstance(children, list):
            stack.extend(children)
        ref = node.get("ref_id")
        if not isinstance(ref, str) or not ref:
            continue
        refs += 1
        role = str(node.get("role", "")).strip().lower()
        actions = {str(item).strip().lower() for item in node.get("available_actions", [])
                   if isinstance(item, str)}
        native = node.get("native_id") if isinstance(node.get("native_id"), dict) else {}
        native_value = str(native.get("value", "")).strip().lower()
        titlebar = native_value == "titlebar" or role in {"title bar", "titlebar"}
        # A generic Group/Pane advertising SetValue is common for title bars
        # and shell surfaces.  Do not speculate that it is a chat composer.
        if not titlebar and role in input_roles:
            composer_inputs += 1
        # Only record a count: button names can contain real conversation text
        # in poorly implemented apps, so never persist them.
        name = str(node.get("name", "")).strip().lower()
        if role == "button" and (name in {"send", "发送", "submit", "提交"} or "send" in name or "发送" in name):
            send_controls += 1
    return {
        "tree_node_count": nodes,
        "tree_ref_count": refs,
        "composer_input_count": composer_inputs,
        "send_control_count": send_controls,
    }


def _combined_tree_status(routes: list[dict]) -> str:
    """Give the best proven route, never promoting an opaque tree to usable."""
    states = {str(route.get("status", "")) for route in routes}
    for state in ("TREE_USABLE", "TREE_PARTIAL", "TREE_OPAQUE", "TREE_TIMEOUT", "TREE_FAILED"):
        if state in states:
            return state
    return "TREE_FAILED"


def _provider_profile(root: Path, provider: str) -> tuple[dict | None, dict | None]:
    """Load an executable provider profile without treating stale MCP discovery as success."""
    name = _name(provider)
    path = _path(root, name)
    if not path.is_file():
        return None, {"status": "PROVIDER_UNREADY", "reason": "PROVIDER_NOT_REGISTERED"}
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, {"status": "PROVIDER_UNREADY", "reason": "PROVIDER_PROFILE_INVALID", "error": _short_error(exc)}
    command = Path(str(profile.get("command", "")))
    if not command.is_file():
        return None, {"status": "PROVIDER_UNREADY", "reason": "PROVIDER_EXECUTABLE_MISSING", "command": str(command)}
    return profile, None


def _select_window(root: Path, agent: str) -> tuple[dict | None, dict]:
    """Resolve exactly one roster window, without activating or reading it."""
    try:
        discovered = client_registry.discover(root, agent)
    except (OSError, ValueError) as exc:
        return None, {"status": "WINDOW_NOT_FOUND", "reason": "CLIENT_DISCOVERY_FAILED", "error": _short_error(exc)}
    clients = discovered.get("clients", []) if isinstance(discovered, dict) else []
    row = clients[0] if len(clients) == 1 and isinstance(clients[0], dict) else {}
    if row.get("out_of_scope"):
        return None, {"status": "OUT_OF_SCOPE", "reason": "CLIENT_OUT_OF_SCOPE", "detail": str(row["out_of_scope"])[:500]}
    windows = row.get("windows", []) if isinstance(row.get("windows"), list) else []
    if not windows:
        return None, {"status": "WINDOW_NOT_FOUND", "reason": "NO_MATCHING_VISIBLE_WINDOW"}
    if len(windows) != 1:
        return None, {"status": "WINDOW_AMBIGUOUS", "reason": "MULTIPLE_MATCHING_WINDOWS", "matches": len(windows)}
    window = windows[0]
    if not isinstance(window, dict):
        return None, {"status": "WINDOW_NOT_FOUND", "reason": "INVALID_WINDOW_RECORD"}
    # A unique application window is still not a proven original conversation.
    # Expose the anchor state separately so operators cannot miss that gap.
    return window, {
        "status": "WINDOW_RESOLVED",
        "identity": str(window.get("identity", "")),
        "anchor_status": str(window.get("anchor_status", "UNCONFIGURED")),
        "anchored": bool(window.get("is_anchor")),
    }


def capability_probe(root: Path, agent: str, *, provider: str = "agent_desktop", timeout: int = 12) -> dict:
    """Read-only per-client capability probe for the original-session pipeline.

    It answers four separate questions: did the roster find one trusted window,
    can the provider bind that same PID/title to one upstream window ID, does
    its accessibility snapshot contain usable refs, and is that window already
    anchored?  It never focuses, types, clicks, sends, or reports an Agent ACK.
    """
    agent = _name(agent)
    provider = _name(provider)
    if provider != "agent_desktop":
        raise ValueError("capability-probe currently supports agent_desktop only")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 30:
        raise ValueError("timeout must be 1..30 seconds")
    result = {
        "schema": "aep-desktop-capability-probe-v1",
        "at": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "provider": provider,
        "read_only": True,
        "desktop_input_performed": False,
        "message_visible": False,
        "agent_ack": False,
        "task_result_readback": False,
        "delivery_verified": False,
        "session_accepted": False,
        "routes": [],
    }
    profile, issue = _provider_profile(root, provider)
    if issue:
        result.update(issue)
        _write(_capability_path(root, provider, agent), result)
        return result
    window, resolution = _select_window(root, agent)
    result["window_resolution"] = resolution
    if not window:
        result.update(status=resolution["status"], reason=resolution.get("reason", ""))
        _write(_capability_path(root, provider, agent), result)
        return result

    command = str(profile["command"])
    listed = _run_json([command, "list-windows"], timeout=timeout)
    if listed.get("ok") is not True or not isinstance(listed.get("data"), list):
        error = listed.get("error") if isinstance(listed.get("error"), dict) else {}
        result.update(status="PROVIDER_UNREADY", reason="WINDOW_LIST_FAILED",
                      provider_error=_short_error(error.get("message", "")))
        _write(_capability_path(root, provider, agent), result)
        return result
    pid = int(window.get("pid") or 0)
    title = str(window.get("title", ""))
    matches = [row for row in listed["data"] if isinstance(row, dict)
               and int(row.get("pid") or 0) == pid and str(row.get("title", "")) == title]
    if not matches:
        result.update(status="WINDOW_NOT_BOUND", reason="UPSTREAM_WINDOW_ID_NOT_FOUND",
                      target_window={"pid": pid, "title": title[:500]})
        _write(_capability_path(root, provider, agent), result)
        return result
    if len(matches) != 1:
        result.update(status="WINDOW_AMBIGUOUS", reason="UPSTREAM_WINDOW_ID_AMBIGUOUS", matches=len(matches))
        _write(_capability_path(root, provider, agent), result)
        return result
    upstream = matches[0]
    window_id = str(upstream.get("id", ""))
    if not window_id:
        result.update(status="WINDOW_NOT_BOUND", reason="UPSTREAM_WINDOW_ID_EMPTY")
        _write(_capability_path(root, provider, agent), result)
        return result
    result["target_window"] = {
        "id": window_id,
        "pid": pid,
        "title": title[:500],
        "process_instance": str(upstream.get("process_instance", ""))[:200],
        "accessible": bool(upstream.get("accessible")),
    }

    base = [command, "snapshot", "--window-id", window_id, "--interactive-only", "--compact",
            "--timeout-ms", str(timeout * 1000)]
    normal = _snapshot_summary(_run_json(base, timeout=timeout + 2), forced_electron_a11y=False)
    result["routes"].append(normal)
    # Chromium/Electron frequently needs an explicit accessibility pass.  This
    # remains read-only and is reported as a separate route so a forced empty
    # tree cannot hide a normal-route timeout or be mistaken for a session.
    try:
        client = client_registry.profile(root, agent)
    except (OSError, ValueError):
        client = {}
    if bool(client.get("electron")):
        forced = _snapshot_summary(_run_json([*base, "--force-electron-a11y"], timeout=timeout + 2),
                                   forced_electron_a11y=True)
        result["routes"].append(forced)

    tree_status = _combined_tree_status(result["routes"])
    result["tree_status"] = tree_status
    if tree_status == "TREE_USABLE":
        result.update(status="SESSION_UNATTESTED",
                      reason="TREE_USABLE_BUT_NO_MESSAGE_VISIBLE_ACK_OR_RESULT")
    else:
        result.update(status=tree_status, reason="ACCESSIBILITY_TREE_NOT_USABLE")
    result["next"] = (
        "calibrate original-session markers and run a bounded driver only after a unique session attestation"
        if tree_status == "TREE_USABLE" else
        "try a client-specific accessibility, CDP, or official API route; do not type through this provider"
    )
    _write(_capability_path(root, provider, agent), result)
    return result


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
