"""Interactive-user Desktop Runner planning and session identity.

The runner is deliberately separate from the commander: it prepares or
restores a concrete GUI session, but never types, clicks, or sends an Agent
message.  The commander remains responsible for the leased, event-ID-bound
delivery step.  This split lets a background Supervisor use the same session
identity without pretending that a process launch is a delivery receipt.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import time
from typing import Any, Callable

from . import client_registry


READY = "READY"
PREVIEW = "PREVIEW"
DESKTOP_SESSION_UNAVAILABLE = "DESKTOP_SESSION_UNAVAILABLE"
AMBIGUOUS_SESSION = "AMBIGUOUS_SESSION"
LAUNCH_FAILED = "LAUNCH_FAILED"
WAIT_TIMEOUT = "WAIT_TIMEOUT"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _windows_session_id(pid: int) -> int | None:
    """Return the Windows Terminal Services session for a process when possible."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        session = ctypes.c_ulong()
        if ctypes.windll.kernel32.ProcessIdToSessionId(int(pid), ctypes.byref(session)):
            return int(session.value)
    except (AttributeError, OSError, ValueError):
        pass
    return None


def _process_facts(pid: int, *, exe: str = "") -> dict[str, Any]:
    facts: dict[str, Any] = {"pid": int(pid), "exe": exe, "process_start_time": "", "user": ""}
    try:
        import psutil
        try:
            process = psutil.Process(int(pid))
            facts["exe"] = facts["exe"] or process.exe()
            facts["process_start_time"] = datetime.fromtimestamp(process.create_time(), timezone.utc).isoformat()
            try:
                facts["user"] = process.username()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError, ValueError):
            # A UI window can outlive its process briefly during restart; the
            # window identity remains useful while process facts are refreshed.
            pass
    except ImportError:
        pass
    facts["windows_session_id"] = _windows_session_id(int(pid))
    return facts


def session_identity(window: dict[str, Any], *, agent: str, exe: str = "") -> dict[str, Any]:
    """Normalize one discovered window into a durable, auditable identity."""
    if not isinstance(window, dict) or not isinstance(agent, str) or not agent:
        raise ValueError("window and agent are required")
    pid = window.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("window.pid must be a positive integer")
    facts = _process_facts(pid, exe=exe)
    return {
        "agent": agent,
        "pid": pid,
        "window_handle": window.get("hwnd", 0),
        "window_title": str(window.get("title", "")),
        "session_id": str(window.get("session_id", "")),
        "process_start_time": facts["process_start_time"],
        "exe": facts["exe"],
        "user": facts["user"],
        "windows_session_id": facts["windows_session_id"],
        "fingerprint": str(window.get("fingerprint", "")),
        "captured_at": _now(),
    }


def _candidate(discovery: dict[str, Any], agent: str) -> tuple[str, dict[str, Any] | None]:
    clients = [row for row in discovery.get("clients", []) if row.get("agent") == agent]
    if not clients:
        return DESKTOP_SESSION_UNAVAILABLE, None
    client = clients[0]
    windows = client.get("windows", [])
    if not windows:
        return DESKTOP_SESSION_UNAVAILABLE, None
    if client.get("anchored"):
        hit = next((row for row in windows if row.get("is_anchor")), None)
        if hit:
            return READY, hit
    if len(windows) != 1:
        return AMBIGUOUS_SESSION, None
    return READY, windows[0]


def wait_for_ready(root: Path, agent: str, *, timeout: float = 30.0,
                   poll_interval: float = 0.25,
                   discover: Callable[[Path, str], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Wait on observable readiness, not a fixed post-launch delay."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
        raise ValueError("timeout must be a non-negative number")
    if isinstance(poll_interval, bool) or not isinstance(poll_interval, (int, float)) or poll_interval <= 0:
        raise ValueError("poll_interval must be positive")
    discover = discover or client_registry.discover
    deadline = time.monotonic() + float(timeout)
    last_status = DESKTOP_SESSION_UNAVAILABLE
    last_detail: dict[str, Any] = {}
    while True:
        try:
            discovery = discover(Path(root), agent)
            status, window = _candidate(discovery, agent)
            last_status = status
            last_detail = {"discovery": discovery}
            if status == READY and window:
                return {"status": READY, "agent": agent,
                        "session": session_identity(window, agent=agent),
                        "observed_at": _now(), "execution_started": False}
            if status == AMBIGUOUS_SESSION:
                return {"status": AMBIGUOUS_SESSION, "agent": agent,
                        "observed_at": _now(), "execution_started": False,
                        "next": "load or set a unique session anchor before input", **last_detail}
        except (OSError, ValueError, TypeError) as exc:
            last_status = DESKTOP_SESSION_UNAVAILABLE
            last_detail = {"error": str(exc)}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"status": WAIT_TIMEOUT if timeout else last_status, "agent": agent,
                    "observed_at": _now(), "execution_started": False, **last_detail}
        time.sleep(min(float(poll_interval), remaining))


def launch(root: Path, agent: str, *, apply: bool = False, allow_launch: bool = False,
           debug_port: int = 0, extra_args: list[str] | None = None) -> dict[str, Any]:
    """Preview or launch a registered client; never sends desktop input."""
    profile = client_registry.profile(Path(root), agent)
    plan = {"agent": agent, "exe": profile.get("exe", ""),
            "args": list(profile.get("launch_args") or []) + list(extra_args or []),
            "debug_port": int(debug_port) if debug_port else 0,
            "execution_started": False}
    if not apply:
        return {"status": PREVIEW, **plan,
                "next": "repeat with apply=true and allow_launch=true after verifying the profile"}
    result = client_registry.launch(Path(root), agent, allow=allow_launch,
                                    extra_args=extra_args, debug_port=debug_port)
    if result.get("launched"):
        return {"status": "LAUNCHED", **result, "execution_started": True,
                "next": "wait_for_ready and bind the returned process to a unique session"}
    return {"status": result.get("reason", LAUNCH_FAILED), **result,
            "execution_started": False}


def ensure_session(root: Path, agent: str, *, apply: bool = False,
                   allow_launch: bool = False, timeout: float = 30.0,
                   poll_interval: float = 0.25, debug_port: int = 0) -> dict[str, Any]:
    """Resolve an existing session or preview/launch then wait for readiness."""
    first = wait_for_ready(root, agent, timeout=0, poll_interval=poll_interval)
    if first.get("status") == READY:
        return first
    if first.get("status") == AMBIGUOUS_SESSION:
        return first
    launch_result = launch(root, agent, apply=apply, allow_launch=allow_launch,
                           debug_port=debug_port)
    if launch_result.get("status") != "LAUNCHED":
        return {"status": DESKTOP_SESSION_UNAVAILABLE if launch_result.get("status") == PREVIEW else launch_result.get("status"),
                "agent": agent, "launch": launch_result, "execution_started": False}
    return wait_for_ready(root, agent, timeout=timeout, poll_interval=poll_interval)
