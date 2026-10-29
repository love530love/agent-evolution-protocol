"""Discover running desktop Agent hosts without assuming a window title."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


KNOWN = {
    "Qoder": "qoder",
    "Qoder CN": "qoder_cn",
    "WorkBuddy": "workbuddy",
    "WorkBuddyAI": "workbuddy_ai",
    "ChatGPT": "chatgpt",
    "ClawChat": "clawchat",
    "CodeBuddy CN": "codebuddy_cn",
    "QClaw": "qclaw",
}


def _windows() -> dict[int, list[str]]:
    if os.name != "nt": return {}
    import ctypes
    user32 = ctypes.windll.user32
    result: dict[int, list[str]] = {}
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd): return True
        pid = ctypes.c_ulong(); user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1); user32.GetWindowTextW(hwnd, buf, n + 1)
            result.setdefault(pid.value, []).append(buf.value)
        return True
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return result


def discover() -> list[dict[str, Any]]:
    try:
        import psutil
    except ImportError:
        return []
    titles = _windows(); rows = []
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = p.info.get("name") or ""
            base = Path(name).stem
            agent = KNOWN.get(name) or KNOWN.get(base)
            if not agent: continue
            rows.append({"agent": agent, "process_name": name, "pid": p.info["pid"],
                         "process_path": p.info.get("exe") or "", "window_titles": titles.get(p.info["pid"], []),
                         "visible": bool(titles.get(p.info["pid"]))})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return rows


def visible_hosts() -> list[dict[str, Any]]:
    """Return one candidate per visible top-level Agent window."""
    rows = [r for r in discover() if r["visible"]]
    return [{**r, "session_hint": f"pid:{r['pid']}"} for r in rows]


def persist(root: str | Path) -> dict[str, Any]:
    """Persist discovery evidence; does not claim registration or acceptance."""
    import json
    p = Path(root) / "clients" / "desktop_discovery.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = visible_hosts()
    payload = {"state": "discovered", "hosts": rows}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload
