"""Registry and discovery of desktop agent clients.

The legion cannot command what it cannot name. This module turns "any agent"
into a concrete, uniquely identified window: it discovers live windows, keeps a
per-agent session anchor so two same-titled windows stop being ambiguous, and
records which transport actually worked for that client.

Discovery and registration are read-only with respect to the target client.
Launching is explicit and gated by ``allow_launch`` plus the environment flag
``AEP_CLIENT_LAUNCH=1``.
"""
from __future__ import annotations

from datetime import datetime, timezone
import ctypes
import ctypes.wintypes as wt
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from agent_evolution_protocol.coordination import process_info

SAFE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# Known desktop agent clients. `window_titles` is matched as a case-insensitive
# substring so renamed windows still resolve; `electron` decides whether we may
# request a remote-debugging port when we are the one launching the client.
BUILTIN: dict[str, dict] = {
    "workbuddy": {
        "display": "WorkBuddy",
        "process": "WorkBuddy",
        "exe": r"D:\Programs\WorkBuddy\WorkBuddy.exe",
        "window_titles": ["WorkBuddy"],
        "electron": True,
        "transports": ["cdp", "uia", "ocr-paste"],
    },
    "qoder": {
        "display": "Qoder",
        "process": "Qoder",
        "exe": r"D:\Programs\Qoder\Qoder.exe",
        # Qoder's updater places the running image below a versioned directory.
        # This is an opt-in boundary, never a fuzzy prefix match: see
        # ``process_info.executable_match``.
        "allowed_exe_roots": [r"D:\Programs\Qoder\.qoder-versions"],
        "window_titles": ["Qoder"],
        "window_titles_exact": True,
        "electron": True,
        "transports": ["cdp", "uia", "ocr-paste"],
    },
    "qoder_cn": {
        "display": "Qoder CN",
        "process": "Qoder CN",
        "exe": r"D:\Programs\QoderCN\Qoder CN\.qoder-versions\0.4.11\Qoder CN.exe",
        "allowed_exe_roots": [r"D:\Programs\QoderCN\Qoder CN\.qoder-versions"],
        "window_titles": ["Qoder CN"],
        "window_titles_exact": True,
        "electron": True,
        "transports": ["uia", "ocr-paste", "cdp"],
    },
    "chatgpt": {
        "display": "ChatGPT",
        "process": "ChatGPT",
        "exe": "",
        "window_titles": ["ChatGPT"],
        "electron": True,
        "transports": ["uia", "ocr-paste"],
    },
    "doubao": {
        "display": "豆包",
        "process": "Doubao",
        "exe": "",
        "window_titles": ["豆包"],
        "electron": True,
        "transports": ["uia", "ocr-paste"],
    },
    "agents": {
        "display": "Agents",
        "process": "",
        "exe": "",
        "window_titles": ["Agents"],
        "electron": True,
        "transports": ["uia", "ocr-paste"],
    },
    "chrome": {
        "display": "Chrome",
        "process": "chrome",
        "exe": "",
        "window_titles": ["Chrome"],
        "electron": True,
        "transports": ["cdp", "uia", "ocr-paste"],
        # A browser tab is not a collaboration surface.  The window this
        # profile matches on this machine is a live web Feishu chat, and both
        # directions are wrong: perceiving it copies an unrelated human
        # conversation into the shared transcript bus, and delivering to it
        # would type into a chat with real people in it.  The rule lives here,
        # in the roster, rather than in a chat message, because a rule that
        # only exists in a conversation is a rule the next session will not
        # know about.
        "out_of_scope": ("web Feishu / browser tabs are outside the collaboration "
                         "surface: reading one pulls an unrelated conversation into "
                         "the bus, and typing into one messages real people"),
    },
}


def _user32():
    u32 = ctypes.windll.user32
    u32.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
    u32.EnumWindows.restype = wt.BOOL
    u32.GetWindowTextW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
    return u32


def _exe_for(pid: int) -> str:
    """Best-effort executable path for a window's process.

    ``QueryFullProcessImageNameW`` answers for elevated processes too, but it is
    still allowed to fail -- a protected process, a race with exit.  An empty
    string therefore means "not known", and every caller treats it that way
    rather than as a mismatch: a probe failure must not make a client silently
    undiscoverable, which is the failure mode that looks like "the app is not
    running".
    """
    from agent_evolution_protocol.coordination import process_info

    if not pid or not process_info.available():
        return ""
    try:
        return process_info.image_path(int(pid))
    except (OSError, ValueError):
        return ""


def _executable_verdict(profile_row: dict, actual: str) -> dict:
    """Apply the same executable policy in discovery and in the UI adapter.

    Desktop clients commonly update beneath a versioned directory.  Discovery
    must recognise a deliberately trusted replacement, but it must never turn
    `Qoder` into a loose prefix that claims `Qoder CN`.
    """
    return process_info.executable_match(
        str(profile_row.get("exe", "")), str(actual or ""),
        allowed_roots=profile_row.get("allowed_exe_roots", ()),
    )


def _same_path(left: str, right: str, *, allowed_roots=()) -> bool:
    return _executable_verdict({"exe": left, "allowed_exe_roots": allowed_roots}, right)["match"]


def _runtime_profile(root: Path, agent: str, row: dict) -> dict:
    """Add explicit UI-profile roots to the registered client identity.

    Client records name the application; ui profiles describe one conversation
    inside it.  The latter may be the first place an operator records an
    updater root, so discovery consumes both while preserving a single
    executable policy for every later layer.
    """
    merged = dict(row)
    roots: list[str] = list(_normalise_allowed_exe_roots(row.get("allowed_exe_roots", ())))
    ui_path = Path(root) / "ui_profiles" / f"{agent}.json"
    if ui_path.is_file():
        try:
            ui_row = json.loads(ui_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ui_row = {}
        source = ui_row.get("allowed_exe_roots", ()) if isinstance(ui_row, dict) else ()
        roots.extend(_normalise_allowed_exe_roots(source))
    # Keep declaration order for audit readability while eliminating repeated
    # roots that would otherwise make profile digests drift pointlessly.
    unique: list[str] = []
    for root_path in roots:
        if root_path not in unique:
            unique.append(root_path)
    merged["allowed_exe_roots"] = _normalise_allowed_exe_roots(unique)
    return merged


def visible_windows(*, probe_exe: bool = True) -> list[dict]:
    """Enumerate visible top-level windows with titles. No UI is touched."""
    u32 = _user32()
    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    rows: list[dict] = []

    def cb(hwnd, _lparam):
        if u32.IsWindowVisible(hwnd):
            length = u32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                u32.GetWindowTextW(hwnd, buf, length + 1)
                pid = wt.DWORD()
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                rows.append({"hwnd": int(hwnd), "pid": int(pid.value), "title": buf.value})
        return True

    u32.EnumWindows(WNDENUMPROC(cb), 0)
    out = [row for row in rows if row["title"].strip()]
    if probe_exe:
        # One lookup per process, not per window: a client with a settings
        # dialog open has several top-level windows and the same executable.
        seen: dict[int, str] = {}
        for row in out:
            pid = row["pid"]
            if pid not in seen:
                seen[pid] = _exe_for(pid)
            row["exe"] = seen[pid]
    return out


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _validate(agent: str) -> str:
    if not SAFE_NAME.fullmatch(agent):
        raise ValueError("invalid agent name")
    return agent


def _normalise_allowed_exe_roots(roots: list[str] | tuple[str, ...] | None) -> list[str]:
    """Compatibility wrapper for the single executable-trust policy."""
    return process_info.normalise_allowed_exe_roots(roots)


def register(root: Path, *, agent: str, display: str = "", process: str = "",
             exe: str = "", window_titles: list[str] | None = None,
             electron: bool = True, transports: list[str] | None = None,
             launch_args: list[str] | None = None, notes: str = "",
              session_label: str = "", allowed_exe_roots: list[str] | None = None) -> dict:
    _validate(agent)
    titles = [item for item in (window_titles or []) if item.strip()]
    if not titles and not process:
        raise ValueError("register needs --window-titles or --process")
    payload = {
        "agent": agent,
        "display": display or agent,
        "process": process,
        "exe": exe,
        "allowed_exe_roots": _normalise_allowed_exe_roots(allowed_exe_roots),
        "window_titles": titles,
        "electron": bool(electron),
        "transports": transports or ["uia", "ocr-paste"],
        "launch_args": list(launch_args or []),
        "notes": notes.strip(),
        "session_label": session_label.strip(),
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(Path(root) / "clients" / f"{agent}.json", payload)
    return payload


def profile(root: Path, agent: str) -> dict:
    _validate(agent)
    path = Path(root) / "clients" / f"{agent}.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["source"] = "registered"
        # A registered profile says how to *reach* a client; it does not say
        # whether the client is in scope.  This branch returns the file
        # verbatim, so without the line below, registering a chrome.json would
        # quietly put the browser tab back into service -- the refusal would
        # live only in the catalogue that the registration just shadowed.
        builtin = BUILTIN.get(agent) or {}
        if builtin.get("out_of_scope"):
            data["out_of_scope"] = builtin["out_of_scope"]
        return data
    if agent in BUILTIN:
        data = dict(BUILTIN[agent])
        data["agent"] = agent
        data.setdefault("launch_args", [])
        data["source"] = "builtin"
        return data
    raise ValueError(f"unknown client '{agent}'; register it first with coord-client register")


def known(root: Path) -> list[str]:
    names = set(BUILTIN)
    folder = Path(root) / "clients"
    if folder.exists():
        names |= {path.stem for path in folder.glob("*.json")}
    return sorted(names)


def matches(profile_row: dict, window: dict) -> bool:
    title = str(window.get("title", "")).lower()
    exact = bool(profile_row.get("window_titles_exact"))
    for wanted in profile_row.get("window_titles", []):
        wanted_lower = wanted.lower()
        if (wanted_lower == title) if exact else (wanted_lower in title):
            return True
    # Exact-title profiles must not fall through to the legacy substring
    # process check: ``Qoder`` is a substring of ``Qoder CN``.
    if exact:
        return False
    process = (profile_row.get("process") or "").lower()
    if process and process in str(window.get("title", "")).lower():
        return True
    return False


def identity(profile_row: dict, window: dict) -> str:
    """How strongly this window was tied to this profile.

    ``exe`` is the only strength that survives two products with nested names,
    because it is the one fact a title cannot forge.  ``title`` and
    ``process-substring`` are progressively weaker guesses, and a dispatcher is
    entitled to demand better than the last one before typing into a window.
    """
    verdict = _executable_verdict(profile_row, str(window.get("exe", "")))
    if verdict["match"]:
        return "exe" if verdict["mode"] == "exact" else "exe-trusted-root"
    title = str(window.get("title", "")).lower()
    exact = bool(profile_row.get("window_titles_exact"))
    for wanted in profile_row.get("window_titles", []):
        wanted_lower = wanted.lower()
        if (wanted_lower == title) if exact else (wanted_lower in title):
            return "title"
    return "process-substring"


def _executable_owners(root: Path, names: list[str]) -> list[dict]:
    """Profiles that explicitly claim executable identities or version roots."""
    owners: list[dict] = []
    for name in names:
        try:
            row = profile(root, name)
        except (ValueError, OSError):
            continue
        row = _runtime_profile(root, name, row)
        if str(row.get("exe", "")):
            owners.append(row)
    return owners


def _settle_by_executable(found: list[dict], owners: list[dict] | None = None) -> list[dict]:
    """Take a window away from every agent whose claim another profile refutes.

    Title matching cannot separate two products whose names nest: measured on
    this machine, the ``qoder`` profile matches the ``Qoder CN`` window because
    ``Qoder`` is a substring of ``Qoder CN``, while ``qoder_cn`` correctly does
    not match ``Qoder`` -- an asymmetry that silently sends Qoder's work into
    Qoder CN whenever only the CN client is running.

    The executable path settles it, but it must not be used as a *filter*: a
    profile pinned to a version directory goes stale the moment the client
    updates, and turning that drift into "no window found" trades a wrong send
    for a client that cannot be reached at all.  So the path is used only to
    resolve a disagreement -- when a window's executable is exactly the one
    another registered profile names, that profile owns it.

    ``owners`` is built from *all* registered profiles, not just the ones being
    discovered: the whole point is to notice that the window belongs to someone
    who was not asked about.
    """
    if owners is None:
        owners = [{"agent": row["agent"], "exe": row.get("exe", ""),
                   "allowed_exe_roots": row.get("allowed_exe_roots", ())}
                  for row in found if row.get("exe")]
    rejected: list[dict] = []
    for row in found:
        kept = []
        for hit in row["windows"]:
            exe = str(hit.get("exe", ""))
            claims = {str(owner["agent"]) for owner in owners if exe and _executable_verdict(owner, exe)["match"]}
            owner = next(iter(claims)) if len(claims) == 1 else None
            if owner and owner != row["agent"]:
                rejected.append({"agent": row["agent"], "hwnd": hit["hwnd"],
                                 "title": hit.get("title", ""), "exe": exe,
                                 "owner": owner,
                                 "reason": "the executable belongs to another registered profile"})
                continue
            kept.append(hit)
        row["windows"] = kept
    return rejected


def discover(root: Path, agent: str = "") -> dict:
    """Find live windows for one agent or the whole roster."""
    windows = visible_windows()
    if agent:
        _validate(agent)
        agents = [agent]
    else:
        agents = known(root)
    found = []
    for name in agents:
        try:
            row = _runtime_profile(root, name, profile(root, name))
        except ValueError:
            continue
        anchor = load_anchor(root, name)
        hits = []
        for window in windows:
            if not matches(row, window):
                continue
            hit = dict(window)
            configured_pid = int(anchor.get("pid") or 0)
            configured_title = str(anchor.get("title") or "")
            pid_match = bool(configured_pid) and hit["pid"] == configured_pid
            title_match = (None if not configured_title else
                           hit.get("title", "") == configured_title)
            # A recycled PID is not an anchor.  The title guard is intentionally
            # simple and visible; a conversation fingerprint is checked by the
            # separate fingerprint command/dispatch preflight, not inferred from
            # a window title here.
            hit["anchor_pid"] = configured_pid or None
            hit["anchor_title_match"] = title_match
            hit["is_anchor"] = pid_match and title_match is not False
            hit["anchor_status"] = (
                "CURRENT" if hit["is_anchor"] else
                "PID_TITLE_MISMATCH" if pid_match else
                "STALE" if configured_pid else "UNCONFIGURED"
            )
            # The old field compared a title to a fingerprint string and always
            # returned false.  Discovery has not read the active transcript, so
            # no fingerprint verdict exists at this layer.  Keep the nullable
            # key for consumers while making its absence explicit.
            hit["fingerprint_match"] = None
            hit["fingerprint_status"] = "NOT_OBSERVED_IN_DISCOVERY"
            hit["agent"] = name
            hit["identity"] = identity(row, window)
            hits.append(hit)
        found.append({
            "agent": name,
            "display": row.get("display", name),
            "electron": row.get("electron", True),
            "transports": row.get("transports", []),
            "exe": row.get("exe", ""),
            "windows": hits,
            "unique": len(hits) == 1,
            "anchored": any(item["is_anchor"] for item in hits),
            "out_of_scope": row.get("out_of_scope", ""),
        })
    rejected = _settle_by_executable(found, _executable_owners(root, known(root)))
    for row in found:
        # Recompute after arbitration: an anchor window that was refuted by
        # another profile's executable is no longer an anchor for this agent.
        row["unique"] = len(row["windows"]) == 1
        row["anchored"] = any(item["is_anchor"] for item in row["windows"])
    return {
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "total_windows": len(windows),
        "clients": found,
        "rejected": rejected,
        "note": "unique=false means the window cannot be targeted safely until an anchor pid or session fingerprint is set.",
    }


def set_anchor(root: Path, *, agent: str, pid: int, title: str = "",
               session_id: str = "", fingerprint: str = "") -> dict:
    """Pin an agent to one concrete window so duplicate titles stop blocking input."""
    _validate(agent)
    payload = {
        "agent": agent, "pid": int(pid), "title": title, "session_id": session_id,
        "fingerprint": fingerprint, "anchored_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(Path(root) / "clients" / "anchors" / f"{agent}.json", payload)
    return payload


def load_anchor(root: Path, agent: str) -> dict:
    path = Path(root) / "clients" / "anchors" / f"{agent}.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def launch_allowed(env: dict | None = None) -> bool:
    return (env or os.environ).get("AEP_CLIENT_LAUNCH") == "1"


def launch(root: Path, agent: str, *, allow: bool = False,
           extra_args: list[str] | None = None, debug_port: int = 0) -> dict:
    """Start a client ourselves so we control its session and transport."""
    row = profile(root, agent)
    exe = row.get("exe", "")
    if not exe or not Path(exe).is_file():
        return {"agent": agent, "launched": False, "reason": "NO_EXE_PATH",
                "hint": "register the client with --exe before asking AEP to open it"}
    if not allow or not launch_allowed():
        return {"agent": agent, "launched": False, "reason": "LAUNCH_NOT_AUTHORISED",
                "hint": "pass --allow-launch and set AEP_CLIENT_LAUNCH=1"}
    args = list(row.get("launch_args") or [])
    if debug_port and row.get("electron"):
        args.append(f"--remote-debugging-port={int(debug_port)}")
    args += list(extra_args or [])
    import subprocess
    try:
        proc = subprocess.Popen([exe, *args], close_fds=True)
    except OSError as exc:
        return {"agent": agent, "launched": False, "reason": "LAUNCH_FAILED", "error": str(exc)}
    return {"agent": agent, "launched": True, "pid": proc.pid, "exe": exe,
            "debug_port": int(debug_port) if debug_port else 0, "args": args,
            "note": "A new window is a new session; anchor it and hand over full context before relying on it."}
