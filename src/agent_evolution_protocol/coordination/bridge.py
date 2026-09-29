"""Atomic file mailbox and task claims for Codex and WorkBuddy."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
AGENTS = ("codex", "workbuddy", "qoder", "codebuddy", "github_copilot", "hermes_desktop", "autoclaw")
# codex/workbuddy/qoder: 外部协作者; codebuddy: 本机宿主(爱丰/FlagGems T105);
# github_copilot: GitHub Copilot 代理, 2026-09-28 接互区。
# hermes_desktop: Hermes Desktop 手动/Hook 入口, 2026-09-29 加入协作。
# autoclaw: AutoClaw/QClaw/OpenClaw 系工具入口, 2026-09-29 加入协作。

# Credentials that gate every direct API call to the competition platform.
# Each entry is (env var, repo-relative path).  The health check only decodes
# the JWT `exp` claim locally -- it never sends a request and never prints the
# token -- so an expired credential is visible before it silently degrades an
# upload into an anonymous read.
CREDENTIALS = (
    ("FLAGOS_AUTH", ROOT.parent / ".flagos_auth.txt"),
)


def credential_health():
    """Report expiry of local credential files.  Offline and read-only.

    A missing file is reported as `missing`, an unparseable one as
    `unreadable`; neither is an error state for the bus itself, so a machine
    without these credentials can still use every other command.
    """
    now = time.time()
    out = {}
    for name, path in CREDENTIALS:
        entry = {"path": str(path)}
        if not path.exists():
            entry.update(state="missing")
            out[name] = entry
            continue
        try:
            token = path.read_text(encoding="utf-8").strip()
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
        except Exception:                                   # noqa: BLE001
            entry.update(state="unreadable")
            out[name] = entry
            continue
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)):
            entry.update(state="no-exp-claim")
            out[name] = entry
            continue
        remaining = exp - now
        entry.update(
            state="expired" if remaining <= 0 else "valid",
            expires_at=datetime.fromtimestamp(exp, timezone.utc)
            .astimezone().isoformat(timespec="seconds"),
            seconds_remaining=int(remaining),
            hours_remaining=round(remaining / 3600, 2),
        )
        if remaining <= 0:
            entry["expired_for_hours"] = round(-remaining / 3600, 2)
        out[name] = entry
    return out



def mirror(event):
    """Best-effort push to the realtime room; never retries or blocks mailbox use."""
    token_file = ROOT / "realtime" / "token.txt"
    if not token_file.exists():
        return
    request = Request(
        "http://127.0.0.1:8765/api/event",
        data=json.dumps(event, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Collab-Token": token_file.read_text(encoding="utf-8").strip()},
        method="POST",
    )
    try:
        with urlopen(request, timeout=0.5):
            pass
    except (OSError, URLError):
        pass


def stamp():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def agent(value):
    if value not in AGENTS:
        raise ValueError("agent must be one of: " + ", ".join(AGENTS))
    return value


def task(value):
    value = value.upper()
    if not re.fullmatch(r"T[0-9]{1,4}(-[A-Z0-9]{1,8})?", value):
        raise ValueError("task must look like T89 or T103-R6Q")
    return value


def phase(value):
    allowed = {
        "todo", "claimed", "working", "waiting", "blocked",
        "needs-review", "ready", "done", "abandoned"
    }
    if value not in allowed:
        raise ValueError("phase must be one of " + ", ".join(sorted(allowed)))
    return value


def unread(who):
    for path in sorted((ROOT / "messages" / who).glob("*.json")):
        if not (ROOT / "acks" / who / path.name).exists():
            yield read(path)


def wake_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]


def execute(a):
    if a.cmd == "send":
        sender, receiver = agent(a.sender), agent(a.to)
        if sender == receiver:
            raise ValueError("sender and receiver must differ")
        body = a.text if a.text is not None else Path(a.text_file).read_text(encoding="utf-8")
        if not body.strip():
            raise ValueError("message body is empty")
        mid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
        msg = dict(id=mid, at=stamp(), sender=sender, to=receiver, topic=a.topic, kind=a.kind, text=body)
        write(ROOT / "messages" / receiver / (mid + ".json"), msg)
        mirror(dict(room="flaggems-sglang", actor=sender, target=receiver, kind=a.kind, text=body, source="legacy-bridge", reply_to=mid))
        print(json.dumps(msg, ensure_ascii=False))
    elif a.cmd == "inbox":
        items = list(unread(agent(a.agent)))
        print(json.dumps(dict(unread_count=len(items), messages=items), ensure_ascii=False, indent=2))
    elif a.cmd == "ack":
        who = agent(a.agent)
        if not re.fullmatch(r"[0-9TZ]+-[0-9a-f]{8}", a.id) or not (ROOT / "messages" / who / (a.id + ".json")).is_file():
            raise ValueError("unknown message ID")
        write(ROOT / "acks" / who / (a.id + ".json"), dict(id=a.id, agent=who, at=stamp()))
        mirror(dict(room="flaggems-sglang", actor=who, kind="ack", text=f"ACK {a.id}", source="legacy-bridge", reply_to=a.id))
        print("ACK", a.id)
    elif a.cmd == "claim":
        who, name = agent(a.agent), task(a.task)
        folder = ROOT / "claims" / name
        try:
            folder.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            owner = folder / "owner.json"
            print(json.dumps(dict(claimed=False, task=name, owner=read(owner) if owner.exists() else "pending"), ensure_ascii=False))
            return 2
        try:
            data = dict(task=name, agent=who, at=stamp(), paths=a.paths or [])
            write(folder / "owner.json", data)
        except BaseException:
            folder.rmdir()
            raise
        print(json.dumps(dict(claimed=True, **data), ensure_ascii=False))
    elif a.cmd == "release":
        who, name = agent(a.agent), task(a.task)
        folder = ROOT / "claims" / name
        if read(folder / "owner.json")["agent"] != who:
            raise ValueError("only owner may release")
        (folder / "owner.json").unlink()
        folder.rmdir()
        print("RELEASED", name)
    elif a.cmd == "status":
        claims = []
        for folder in sorted((ROOT / "claims").glob("T*")):
            owner = folder / "owner.json"
            claims.append(read(owner) if owner.exists() else dict(task=folder.name, state="pending"))
        board = []
        for path in sorted((ROOT / "task_states").glob("*.json")):
            board.append(read(path))
        print(json.dumps(dict(claims=claims, board=board, credentials=credential_health(), unread={who: len(list(unread(who))) for who in AGENTS}), ensure_ascii=False, indent=2))
    elif a.cmd == "board":
        board = []
        for path in sorted((ROOT / "task_states").glob("*.json")):
            row = read(path)
            if a.agent and row.get("owner") != agent(a.agent):
                continue
            board.append(row)
        print(json.dumps(dict(tasks=board), ensure_ascii=False, indent=2))
    elif a.cmd == "cred-check":
        health = credential_health()
        print(json.dumps(health, ensure_ascii=False, indent=2))
        # Non-zero only for a credential that exists and cannot be used, so a
        # script can gate an upload on this without treating "not configured
        # here" as a failure.
        return 1 if any(e["state"] in ("expired", "unreadable", "no-exp-claim")
                        for e in health.values()) else 0
    elif a.cmd == "task-state":
        name = task(a.task)
        who = agent(a.agent)
        state = dict(
            task=name,
            owner=who,
            phase=phase(a.phase),
            at=stamp(),
            goal=a.goal,
            evidence=a.evidence or [],
            blocked_reason=a.blocked_reason or "",
            next_action=a.next_action or "",
            wake_phrase=a.wake_phrase or f"@wake {who}",
            human_required=bool(a.human_required),
        )
        write(ROOT / "task_states" / (name + ".json"), state)
        mirror(dict(room="flaggems-sglang", actor=who, kind="task-state", text=json.dumps(state, ensure_ascii=False), source="legacy-bridge", task=name))
        print(json.dumps(state, ensure_ascii=False, indent=2))
    elif a.cmd == "wake":
        sender, receiver = agent(a.sender), agent(a.to)
        if sender == receiver:
            raise ValueError("sender and receiver must differ")
        name = task(a.task)
        if not a.reason.strip():
            raise ValueError("wake reason is empty")
        wid = wake_id()
        event = dict(
            id=wid,
            type="wake_request",
            at=stamp(),
            sender=sender,
            to=receiver,
            task=name,
            reason=a.reason,
            budget=a.budget,
            entrypoint=a.entrypoint or "",
            requires_user_auth=bool(a.requires_user_auth),
            no_polling=True,
            dry_run=bool(a.dry_run),
        )
        write(ROOT / "wake_queue" / (wid + ".json"), event)
        mirror(dict(room="flaggems-sglang", actor=sender, target=receiver, kind="wake_request", text=json.dumps(event, ensure_ascii=False), source="legacy-bridge", task=name, reply_to=wid))
        print(json.dumps(event, ensure_ascii=False, indent=2))
    elif a.cmd == "wake-status":
        def count(folder):
            path = ROOT / folder
            return len(list(path.glob("*.json"))) if path.exists() else 0

        log_path = ROOT / "wake_log.jsonl"
        tail = []
        if log_path.exists():
            tail = log_path.read_text(encoding="utf-8").splitlines()[-a.tail:]
            tail = [json.loads(line) for line in tail if line.strip()]
        print(json.dumps(dict(
            queue=count("wake_queue"),
            done=count("wake_done"),
            failed=count("wake_failed"),
            log_tail=tail,
        ), ensure_ascii=False, indent=2))
    elif a.cmd == "watch":
        who = agent(a.agent)
        seen = set()
        end = time.monotonic() + a.seconds
        while True:
            for msg in unread(who):
                if msg["id"] not in seen:
                    print(json.dumps(msg, ensure_ascii=False), flush=True)
                    seen.add(msg["id"])
            if time.monotonic() >= end:
                break
            time.sleep(min(a.interval, end - time.monotonic()))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("send")
    x.add_argument("--from", dest="sender", required=True)
    x.add_argument("--to", required=True)
    x.add_argument("--topic", required=True)
    x.add_argument("--kind", choices=("update", "proposal", "question", "result", "warning", "context"), required=True)
    body = x.add_mutually_exclusive_group(required=True)
    body.add_argument("--text")
    body.add_argument("--text-file")
    for name in ("inbox", "status", "watch", "board", "cred-check", "wake-status"):
        x = sub.add_parser(name)
        if name not in ("status", "board", "cred-check", "wake-status"):
            x.add_argument("--agent", required=True)
        if name == "board":
            x.add_argument("--agent")
        if name == "watch":
            x.add_argument("--seconds", type=int, default=300)
            x.add_argument("--interval", type=float, default=5)
        if name == "wake-status":
            x.add_argument("--tail", type=int, default=10)
    x = sub.add_parser("task-state")
    x.add_argument("--agent", required=True)
    x.add_argument("--task", required=True)
    x.add_argument("--phase", required=True)
    x.add_argument("--goal", required=True)
    x.add_argument("--evidence", nargs="*")
    x.add_argument("--blocked-reason")
    x.add_argument("--next-action")
    x.add_argument("--wake-phrase")
    x.add_argument("--human-required", action="store_true")
    x = sub.add_parser("wake")
    x.add_argument("--from", dest="sender", required=True)
    x.add_argument("--to", required=True)
    x.add_argument("--task", required=True)
    x.add_argument("--reason", required=True)
    x.add_argument("--budget", choices=("one-shot", "bounded", "manual"), default="one-shot")
    x.add_argument("--entrypoint")
    x.add_argument("--requires-user-auth", action="store_true")
    x.add_argument("--dry-run", action="store_true")
    x = sub.add_parser("ack")
    x.add_argument("--agent", required=True)
    x.add_argument("--id", required=True)
    x = sub.add_parser("claim")
    x.add_argument("--agent", required=True)
    x.add_argument("--task", required=True)
    x.add_argument("--paths", nargs="*")
    x = sub.add_parser("release")
    x.add_argument("--agent", required=True)
    x.add_argument("--task", required=True)
    a = p.parse_args()
    if getattr(a, "seconds", 0) < 0 or getattr(a, "interval", 1) <= 0:
        p.error("seconds must be nonnegative; interval must be positive")
    try:
        return execute(a)
    except (ValueError, OSError, KeyError) as e:
        print("ERROR:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
