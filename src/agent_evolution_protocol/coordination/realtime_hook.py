"""Hook adapter for Codex/WorkBuddy collaboration events.

Reads one hook JSON object from stdin. It performs local HTTP I/O only and
never calls a model. Exact wake phrases are the only path that requests agent
execution:

    @wake codex: task
    @wake workbuddy: task
    @唤醒 codex: task
    @唤醒 workbuddy: task
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen

from agent_evolution_protocol.coordination.conversation_journal import append_event


ROOT = Path(__file__).resolve().parent
TOKEN_FILE = ROOT / "realtime" / "token.txt"
SCOPE_ROOT = Path(os.environ.get("AEP_WORKSPACE_ROOT") or ROOT.parents[2]).resolve()
JOURNAL_ROOT = Path(os.environ.get("AEP_COORD_ROOT") or ROOT)
WAKE_RE = re.compile(r"^\s*@(?:wake|唤醒)\s+(codex|workbuddy)\s*[:：]\s*(.+)$", re.IGNORECASE | re.DOTALL)


def post(path: str, payload: dict) -> bool:
    if not TOKEN_FILE.exists():
        return False
    request = Request(
        "http://127.0.0.1:8765" + path,
        data=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Collab-Token": TOKEN_FILE.read_text(encoding="utf-8").strip()},
        method="POST",
    )
    try:
        with urlopen(request, timeout=0.8) as response:
            return response.status == 201
    except (OSError, URLError):
        return False


def in_scope(cwd: str) -> bool:
    """Only sync this repository; user-level hooks must not leak other work."""
    if not cwd:
        return False
    try:
        return Path(cwd).resolve().is_relative_to(SCOPE_ROOT)
    except (OSError, ValueError):
        return False


def save_local(*, room: str, actor: str, source: str, kind: str,
               text: str, session_id: str, cwd: str, raw_hook: dict) -> None:
    try:
        append_event(JOURNAL_ROOT, room=room, actor=actor, source=source,
                     kind=kind, text=text, session_id=session_id, cwd=cwd,
                     raw_hook=raw_hook)
    except (OSError, ValueError, TypeError) as exc:
        print(f"conversation archive failed: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True, choices=("codex", "workbuddy"))
    parser.add_argument("--event", required=True, choices=("prompt", "stop", "start", "end"))
    parser.add_argument("--room", default="flaggems-sglang")
    args = parser.parse_args()
    try:
        stdin_buffer = getattr(sys.stdin, "buffer", None)
        data = json.loads(stdin_buffer.read().decode("utf-8") if stdin_buffer else sys.stdin.read())
    except (UnicodeDecodeError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
    cwd = str(data.get("cwd") or data.get("project_dir") or "")
    session_id = str(data.get("session_id") or "")
    if not in_scope(cwd):
        if args.event == "stop":
            print("{}")
        return 0

    if args.event == "prompt":
        text = str(
            data.get("prompt")
            or data.get("user_prompt")
            or data.get("message")
            or extra.get("user_message")
            or extra.get("prompt")
            or ""
        ).strip()
        save_local(room=args.room, actor="user", source=args.agent, kind="message",
                   text=text, session_id=session_id, cwd=cwd, raw_hook=data)
        if text:
            post("/api/event", {"room": args.room, "actor": "user", "kind": "message", "text": text, "source": args.agent, "cwd": cwd, "session_id": session_id})
            match = WAKE_RE.match(text)
            if match:
                target, task = match.groups()
                post("/api/wake", {
                    "room": args.room, "actor": "user", "target": target.lower(),
                    "text": task.strip(), "execute": True, "cwd": cwd,
                    "source": args.agent, "source_session_id": session_id,
                })
    elif args.event == "stop":
        text = str(
            data.get("last_assistant_message")
            or data.get("lastAssistantMessage")
            or extra.get("assistant_message")
            or extra.get("assistant_response")
            or extra.get("response")
            or ""
        ).strip()
        save_local(room=args.room, actor=args.agent, source=args.agent, kind="result",
                   text=text, session_id=session_id, cwd=cwd, raw_hook=data)
        try:
            from agent_evolution_protocol.coordination.presence import signal
            signal(JOURNAL_ROOT, agent=args.agent, state="online",
                   source="session_hook:reply", session_id=session_id)
        except (OSError, ValueError) as exc:
            print(f"presence update failed: {exc}", file=sys.stderr)
        if text:
            post("/api/event", {"room": args.room, "actor": args.agent, "kind": "result", "text": text, "cwd": cwd, "session_id": session_id})
        print("{}")
    else:
        save_local(room=args.room, actor=args.agent, source=args.agent, kind="status",
                   text=args.event, session_id=session_id, cwd=cwd, raw_hook=data)
        if args.event in {"start", "end"}:
            try:
                from agent_evolution_protocol.coordination.presence import signal
                signal(JOURNAL_ROOT, agent=args.agent,
                       state="online" if args.event == "start" else "offline",
                       source=f"session_hook:{args.event}", session_id=session_id)
            except (OSError, ValueError) as exc:
                print(f"presence update failed: {exc}", file=sys.stderr)
        post("/api/event", {"room": args.room, "actor": args.agent, "kind": "status", "state": "online" if args.event == "start" else "offline", "text": args.event, "cwd": cwd, "session_id": session_id})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
