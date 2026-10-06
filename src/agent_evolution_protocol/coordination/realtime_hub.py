"""Local event-driven collaboration hub for Codex, WorkBuddy, and the user.

The hub never calls a model on a timer. Agent execution only happens after an
explicit ``execute`` wake event (dashboard button, CLI, or exact wake phrase).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

try:
    from cluster_kernel import ClusterKernel
except ImportError:  # imported as competition.coordination.realtime_hub
    try:
        from agent_evolution_protocol.coordination.cluster_kernel import ClusterKernel
    except ImportError:
        from competition.coordination.cluster_kernel import ClusterKernel


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "realtime"
EVENTS = RUNTIME / "events.jsonl"
TOKEN_FILE = RUNTIME / "token.txt"
DASHBOARD = ROOT / "realtime_dashboard.html"
DEFAULT_ROOM = "flaggems-sglang"
ALLOWED_ACTORS = {"user", "codex", "workbuddy", "qoder", "codebuddy", "github_copilot", "system"}
ALLOWED_TARGETS = {"codex", "workbuddy"}  # auto-wake exec only for codex/workbuddy;
# github_copilot/qoder/codebuddy can still post events / be targeted in the board.
KERNEL = ClusterKernel(RUNTIME / "cluster")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def load_or_create_token() -> str:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    token = secrets.token_urlsafe(32)
    atomic_write(TOKEN_FILE, token + "\n")
    return token


class EventStore:
    def __init__(self) -> None:
        self.lock = threading.Condition()
        self.events: list[dict] = []
        self.by_id: dict[str, dict] = {}
        RUNTIME.mkdir(parents=True, exist_ok=True)
        if EVENTS.exists():
            for line in EVENTS.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("id"):
                    self.events.append(event)
                    self.by_id[event["id"]] = event

    def append(self, data: dict) -> dict:
        event = {
            "id": f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:8]}",
            "at": now(),
            **data,
        }
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            with EVENTS.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self.events.append(event)
            self.by_id[event["id"]] = event
            self.lock.notify_all()
        return event

    def snapshot(self, room: str, limit: int = 500) -> list[dict]:
        with self.lock:
            return [item for item in self.events if item.get("room") == room][-limit:]

    def pending(self, room: str, since: str) -> list[dict]:
        """Resume by append order, including gaps larger than the UI snapshot."""
        with self.lock:
            if not since:
                return self.snapshot(room)
            cursor = next((i for i, event in enumerate(self.events) if event["id"] == since), None)
            if cursor is None:
                # Unknown cursors replay the room; clients deduplicate by ID.
                return [event for event in self.events if event.get("room") == room]
            return [event for event in self.events[cursor + 1:] if event.get("room") == room]


STORE = EventStore()
TOKEN = load_or_create_token()


def validate_event(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("JSON object required")
    room = str(data.get("room") or DEFAULT_ROOM).strip()
    actor = str(data.get("actor") or "user").strip().lower()
    kind = str(data.get("kind") or "message").strip().lower()
    text = str(data.get("text") or "").strip()
    if not room or len(room) > 100:
        raise ValueError("invalid room")
    if actor not in ALLOWED_ACTORS:
        raise ValueError("invalid actor")
    if kind not in {"message", "context", "status", "ack", "decision", "result", "warning", "wake"}:
        raise ValueError("invalid kind")
    if not text or len(text) > 200_000:
        raise ValueError("text must contain 1..200000 characters")
    result = {"room": room, "actor": actor, "kind": kind, "text": text}
    for key in ("target", "reply_to", "session_id", "source_session_id", "target_session_id", "cwd", "state", "source"):
        if data.get(key) is not None:
            result[key] = str(data[key])[:1000]
    if data.get("allow_new_session") is True:
        result["allow_new_session"] = True
    return result


def remember_session(event: dict) -> None:
    """Remember only the emitting agent's session; never assign it to a target."""
    session_id = str(event.get("session_id") or "").strip()
    cwd = str(event.get("cwd") or "").strip()
    source = str(event.get("source") or event.get("actor") or "").lower()
    if session_id and cwd and source in {"codex", "workbuddy"}:
        provider = "hermes" if source == "workbuddy" else "codex"
        KERNEL.bind_session(source, provider, cwd, session_id)


def worker_prompt(event: dict) -> str:
    recent = STORE.snapshot(event["room"], 20)
    transcript = "\n".join(
        f"[{item.get('at')}] {item.get('actor')}/{item.get('kind')}: {item.get('text')}"
        for item in recent
        if item["id"] != event["id"]
    )
    return (
        "You were explicitly awakened by the user through the local collaboration room. "
        "Do not poll or wait. Handle the requested task once, then stop. Publish a concise, "
        "evidence-based response.\n\n"
        f"Room: {event['room']}\nWorkspace: {event.get('cwd', '')}\n"
        f"Request: {event['text']}\n\nRecent shared context:\n{transcript[-24000:]}"
    )


def continuation_command(target: str, cwd: Path, prompt: str, plan) -> tuple[list[str], str | None]:
    """Build an exact-session command; never guesses ``last`` or ``latest``."""
    if plan.action == "RESUME_EXISTING" and target == "codex":
        return (["codex", "queue", "--thread", str(plan.session_id), "--message", prompt, "-C", str(cwd), "-s", "workspace-write"], None)
    if plan.action == "RESUME_EXISTING":
        return (["hermes", "--resume", str(plan.session_id), "--in", str(cwd), "--oneshot", prompt], None)
    if target == "codex":
        return (["codex", "exec", "-C", str(cwd), "-s", "workspace-write", "-"], prompt)
    return (["hermes", "--in", str(cwd), "--oneshot", prompt], None)


def launch_worker(event: dict) -> None:
    target = event["target"]
    if event.get("source") == target and event.get("source_session_id"):
        STORE.append({
            "room": event["room"], "actor": target, "kind": "ack",
            "state": "already_in_current_turn", "reply_to": event["id"],
            "session_id": event["source_session_id"],
            "text": "Wake is already present in this agent's current turn; duplicate delivery suppressed.",
        })
        return
    cwd = Path(event.get("cwd") or ROOT.parents[1]).resolve()
    if not cwd.is_dir():
        STORE.append({"room": event["room"], "actor": "system", "kind": "warning", "text": f"Wake failed: invalid cwd {cwd}", "reply_to": event["id"]})
        return
    prompt = worker_prompt(event)
    output_path = RUNTIME / "runs" / f"{event['id']}-{target}.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    allow_new = event.get("allow_new_session") is True
    plan = KERNEL.continuation_plan(
        target,
        str(cwd),
        requested_session_id=str(event.get("target_session_id") or ""),
        allow_new_session=allow_new,
    )
    if plan.action == "BLOCKED_HANDOFF_REQUIRED":
        STORE.append({
            "room": event["room"], "actor": "system", "kind": "warning",
            "state": "handoff_required", "reply_to": event["id"],
            "text": "Wake not executed: no trusted target session. Bind/resume the existing session, or explicitly allow a new session with a complete checkpoint handoff.",
        })
        return
    command, stdin_text = continuation_command(target, cwd, prompt, plan)

    def run() -> None:
        STORE.append({"room": event["room"], "actor": target, "kind": "status", "state": "running", "text": f"Explicit wake accepted; continuation={plan.action.lower()}.", "reply_to": event["id"], "session_id": plan.session_id or ""})
        try:
            completed = subprocess.run(
                command,
                input=stdin_text,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(cwd),
                capture_output=True,
                timeout=1800,
                shell=False,
            )
            combined = (completed.stdout or "") + (("\nSTDERR:\n" + completed.stderr) if completed.stderr else "")
            atomic_write(output_path, combined)
            if plan.action == "RESUME_EXISTING" and target == "codex":
                STORE.append({
                    "room": event["room"], "actor": target,
                    "kind": "ack" if completed.returncode == 0 else "warning",
                    "state": "queued" if completed.returncode == 0 else "delivery_unknown",
                    "text": "Message queued to the existing Codex thread; completion must arrive through its stop hook." if completed.returncode == 0 else (completed.stderr or f"Queue exited {completed.returncode}")[-12000:],
                    "reply_to": event["id"], "source": str(output_path), "session_id": plan.session_id or "",
                })
            else:
                STORE.append({
                    "room": event["room"], "actor": target,
                    "kind": "result" if completed.returncode == 0 else "warning",
                    "state": "idle" if completed.returncode == 0 else "worker_failed",
                    "text": (completed.stdout or completed.stderr or f"Worker exited {completed.returncode}")[-12000:],
                    "reply_to": event["id"], "source": str(output_path), "session_id": plan.session_id or "",
                })
        except subprocess.TimeoutExpired as exc:
            KERNEL.record_state("executor", "UNRESPONSIVE", task_id=event["id"], actor="system", timeout_seconds=exc.timeout)
            KERNEL.record_state("task", "SUSPECTED_STALL", task_id=event["id"], actor="system", reason="deadline elapsed without completion event")
            STORE.append({"room": event["room"], "actor": target, "kind": "warning", "state": "suspected_stall", "text": "Worker deadline elapsed. Intent remains active; this is not user cancellation or proven task failure. Reconcile side effects before retry.", "reply_to": event["id"]})
        except Exception as exc:  # noqa: BLE001 - must record worker failures
            KERNEL.record_state("channel", "DEGRADED", task_id=event["id"], actor="system", error_type=type(exc).__name__)
            STORE.append({"room": event["room"], "actor": target, "kind": "warning", "state": "channel_degraded", "text": f"Wake delivery failed: {type(exc).__name__}: {exc}. Intent remains active.", "reply_to": event["id"]})

    threading.Thread(target=run, name=f"wake-{target}-{event['id']}", daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server_version = "CollabHub/1.0"

    def handle(self) -> None:
        # A disconnected client must not turn an already persisted event into
        # an apparent server failure or produce a traceback on every reconnect.
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{now()}] {self.address_string()} {fmt % args}")

    def send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1_000_000:
            raise ValueError("invalid body length")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def authorized(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Collab-Token", ""), TOKEN)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            with STORE.lock:
                count = len(STORE.events)
            self.send_json(HTTPStatus.OK, {"service": "collaboration-hub", "status": "ok", "pid": os.getpid(), "events": count})
            return
        if parsed.path in {"/", "/index.html"}:
            body = DASHBOARD.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/bootstrap":
            room = parse_qs(parsed.query).get("room", [DEFAULT_ROOM])[0]
            self.send_json(HTTPStatus.OK, {"room": room, "token": TOKEN, "events": STORE.snapshot(room)})
            return
        if parsed.path == "/api/events":
            room = parse_qs(parsed.query).get("room", [DEFAULT_ROOM])[0]
            since = self.headers.get("Last-Event-ID", "")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            sent = False
            try:
                while True:
                    with STORE.lock:
                        candidates = STORE.pending(room, since)
                        if not since and sent:
                            candidates = []
                        if not candidates:
                            STORE.lock.wait(timeout=25)
                            candidates = STORE.pending(room, since)
                            if not since and sent:
                                candidates = []
                    # Never hold the event-store lock while writing to a socket.
                    if not candidates:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    for event in candidates:
                        data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                        self.wfile.write(f"id: {event['id']}\ndata: {data}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        since = event["id"]
                        sent = True
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if not self.authorized():
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid token"})
            return
        try:
            data = self.read_json()
            if self.path == "/api/event":
                event = STORE.append(validate_event(data))
                remember_session(event)
            elif self.path == "/api/wake":
                target = str(data.get("target") or "").lower()
                if target not in ALLOWED_TARGETS:
                    raise ValueError("target must be codex or workbuddy")
                event = STORE.append(validate_event({**data, "actor": data.get("actor", "user"), "kind": "wake", "target": target}))
                if data.get("execute") is True:
                    launch_worker(event)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self.send_json(HTTPStatus.CREATED, event)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("hub is intentionally localhost-only")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(json.dumps({"url": f"http://{args.host}:{args.port}", "room": DEFAULT_ROOM, "events": str(EVENTS)}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
