"""Bounded unattended relay loop for one explicitly bound original chat.

One tick performs at most one write (deliver) or one readback (rollout result).
It never opens a new chat, never retries an uncertain write, and never turns a
visible message into an ACK.  The state file is the authority; a scheduled task
only asks this module to advance that state by one step every 30 minutes.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable

from agent_evolution_protocol.coordination import codex_rollout

SCHEMA = "aep-nightly-loop-v1"
CONFIG_SCHEMA = "aep-nightly-loop-config-v1"
PROVEN_NOT_SENT = {
    "no_window", "no_input_control", "input-not-confirmed",
    "send-control-not-found", "composer-not-confirmed",
    "composer-confirmed-no-send", "composer-region-unsupported",
    "session-not-found", "session-not-verified", "model-not-verified",
    "window-or-input-ambiguous", "uia3-write-failed",
    "transcript-captured", "window-focused-no-delivery",
}
DEFAULT_RESULT_GRACE_SECONDS = 1500.0
DEFAULT_MAX_SENDS = 12


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    os.replace(temp, path)


def _digest_json(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def paths(root: Path) -> dict[str, Path]:
    base = Path(root) / "nightly_loop"
    return {
        "base": base,
        "state": base / "state.json",
        "report": base / "report.md",
        "status": base / "status.json",
        "lock": base / "tick.lock",
        "outbox": base / "outbox",
    }


def load_config(path: Path) -> dict[str, Any]:
    config = _read_json(Path(path))
    if config.get("schema") != CONFIG_SCHEMA:
        raise ValueError(f"config schema must be {CONFIG_SCHEMA}")
    required = ("run_id", "agent", "bound_thread_id", "title_marker",
                "cwd_marker", "peer_repo", "fingerprint", "tasks")
    missing = [name for name in required if not config.get(name)]
    if missing:
        raise ValueError("config missing: " + ", ".join(missing))
    if not isinstance(config["tasks"], list) or not config["tasks"]:
        raise ValueError("config tasks must be a non-empty list")
    for index, task in enumerate(config["tasks"]):
        if not isinstance(task, dict) or not task.get("title") or not task.get("prompt"):
            raise ValueError(f"task {index} needs title and prompt")
    return config


def new_state(config: dict[str, Any], now: datetime) -> dict[str, Any]:
    run_id = str(config["run_id"])
    items = []
    for index, task in enumerate(config["tasks"], start=1):
        event_id = f"AEP-NL-{run_id}-{index:02d}"
        items.append({
            "task_id": task.get("task_id") or f"N{index:02d}",
            "title": str(task["title"]),
            "prompt": str(task["prompt"]),
            "event_id": event_id,
            "marker": f"AEP-RESULT:{event_id}",
            "status": "queued",
            "sent_at": "",
            "receipt": None,
            "result": None,
        })
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "started_at": "",
        "deadline": "",
        "finished": False,
        "stopped": False,
        "status": "INITIALIZED",
        "reason": "",
        "bound_thread_id": str(config["bound_thread_id"]),
        "title_marker": str(config["title_marker"]),
        "cwd_marker": str(config["cwd_marker"]),
        "fingerprint": str(config["fingerprint"]),
        "cursor": 0,
        "send_count": 0,
        "items": items,
        "history": [],
    }


def load_state(root: Path, config: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    target = paths(root)["state"]
    if not target.is_file():
        created = new_state(config, now or _now())
        _write_json(target, created)
        return created
    state = _read_json(target)
    if state.get("schema") != SCHEMA:
        raise ValueError(f"state schema must be {SCHEMA}")
    return state


@contextmanager
def _tick_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                locked = True
            except OSError:
                locked = False
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                locked = False
        yield locked
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _strip_space(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _report_of(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("report"), dict):
        return payload["report"]
    return payload if isinstance(payload, dict) else {}


def _aep_command() -> list[str]:
    installed = Path(sys.executable).with_name("aep.exe")
    if installed.is_file():
        return [str(installed)]
    return [sys.executable, "-c",
            "from agent_evolution_protocol.cli import main; import sys; sys.exit(main())"]


class SystemAdapter:
    """The only class allowed to touch the desktop or the Codex state DB."""

    def __init__(self, root: Path, config: dict[str, Any]):
        self.root = Path(root)
        self.config = config

    def _run(self, args: list[str], timeout: int) -> dict[str, Any]:
        completed = subprocess.run(
            _aep_command() + args, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
        text = completed.stdout.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"status": f"CLI_EXIT_{completed.returncode}",
                    "task_delivered": False, "stderr": completed.stderr[-1200:]}

    def observe(self) -> dict[str, Any]:
        out_file = paths(self.root)["base"] / "observe.txt"
        args = ["coord-uia", "observe", "--agent", str(self.config["agent"]),
                "--out-file", str(out_file)]
        if self.config.get("exe"):
            args += ["--exe", str(self.config["exe"])]
        args.append("--ocr")
        payload = self._run(args, timeout=180)
        report = _report_of(payload)
        text = ""
        try:
            text = out_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pass
        report["transcript_text"] = text
        report["transcript_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return report

    def deliver(self, event_id: str, text: str) -> dict[str, Any]:
        outbox = paths(self.root)["outbox"]
        outbox.mkdir(parents=True, exist_ok=True)
        message = outbox / f"{event_id}.txt"
        message.write_text(text, encoding="utf-8")
        args = ["coord-uia", "deliver", "--agent", str(self.config["agent"]),
                "--event-id", event_id, "--text-file", str(message),
                "--timeout", "180", "--ocr"]
        if self.config.get("exe"):
            args += ["--exe", str(self.config["exe"])]
        return self._run(args, timeout=300)

    def threads(self) -> list[dict[str, Any]]:
        return codex_rollout.find_threads(
            Path(self.config.get("state_db") or codex_rollout.DEFAULT_STATE_DB),
            limit=5)

    def result(self, thread_id: str, marker: str, since: str) -> dict[str, Any]:
        return codex_rollout.result_for_thread(
            Path(self.config.get("state_db") or codex_rollout.DEFAULT_STATE_DB),
            thread_id, marker, since=since)

    def git_snapshot(self, repo: Path) -> dict[str, Any]:
        def run(*args: str) -> str:
            try:
                return subprocess.run(
                    ["git", "-C", str(repo), *args], capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    timeout=30).stdout.strip()
            except OSError:
                return ""
        return {
            "head": run("rev-parse", "HEAD"),
            "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "status": run("status", "--short"),
            "log": run("log", "--oneline", "-5"),
        }


def _preflight(state: dict[str, Any], adapter: Any) -> tuple[bool, dict[str, Any]]:
    bound = str(state["bound_thread_id"])
    threads = adapter.threads()
    row = next((item for item in threads if item.get("thread_id") == bound), None)
    if row is None:
        return False, {"reason": "BOUND_THREAD_NOT_FOUND", "threads": threads}
    if threads and int(threads[0].get("updated_at") or 0) != int(row.get("updated_at") or 0):
        return False, {"reason": "BOUND_THREAD_NOT_NEWEST",
                       "newest": threads[0].get("thread_id"), "threads": threads}
    if _strip_space(state["title_marker"]) not in _strip_space(row.get("title")):
        return False, {"reason": "TITLE_MARKER_MISSING"}
    if _strip_space(state["cwd_marker"]) not in _strip_space(row.get("cwd")):
        return False, {"reason": "CWD_MARKER_MISSING"}
    observation = adapter.observe()
    activity = observation.get("activity_evidence") or {}
    if activity.get("state") != "ready":
        return False, {"reason": "CLIENT_NOT_READY", "activity": activity}
    if observation.get("input_found") is not True:
        return False, {"reason": "COMPOSER_NOT_FOUND"}
    if observation.get("drivable") is not True:
        return False, {"reason": "CLIENT_NOT_DRIVABLE"}
    fingerprint = str(state.get("fingerprint") or "")
    transcript = str(observation.get("transcript_text") or "")
    if not fingerprint or _strip_space(fingerprint) not in _strip_space(transcript):
        return False, {"reason": "ACTIVE_VIEW_FINGERPRINT_MISSING",
                       "fingerprint": fingerprint,
                       "transcript_sha256": observation.get("transcript_sha256", "")}
    return True, {"reason": "READY",
                  "transcript_sha256": observation.get("transcript_sha256", ""),
                  "pid": observation.get("pid")}


def _extract(message: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key in ("commit", "tests", "changed", "unresolved"):
        match = re.search(rf"^{key}\s*:\s*(.+)$", message, re.MULTILINE)
        if match:
            fields[key] = match.group(1).strip()
    return fields


def _record(state: dict[str, Any], action: str, payload: dict[str, Any],
            now: datetime) -> None:
    state["updated_at"] = _iso(now)
    state.setdefault("history", []).append({
        "at": _iso(now), "action": action, "payload": payload,
    })
    state["history"] = state["history"][-200:]


def _finalize_report(root: Path, state: dict[str, Any], adapter: Any,
                     now: datetime) -> dict[str, Any]:
    target = paths(root)
    aep = adapter.git_snapshot(Path(root))
    peer = adapter.git_snapshot(Path(state.get("peer_repo") or ""))
    lines = [
        "# AEP × AEGS 无人值守协作闭环 · 早间报告",
        "",
        f"- 运行 ID：`{state['run_id']}`",
        f"- 状态：**{state['status']}**",
        f"- 窗口：`{state.get('started_at') or '未启动'}` → `{state.get('deadline') or '未启动'}`",
        f"- 生成时间：`{_iso(now)}`",
        f"- 绑定线程：`{state['bound_thread_id']}`",
        f"- 活动指纹：`{state.get('fingerprint', '')}`",
        f"- 已发送：{state.get('send_count', 0)} 轮；队列游标：{state.get('cursor', 0)}/{len(state.get('items', []))}",
        f"- 停止原因：`{state.get('reason') or 'none'}`",
        "",
        "## 轮次",
        "",
        "| # | 事件 ID | 标题 | 状态 | 发送时间 | 回读时间 | commit | tests |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(state.get("items", []), start=1):
        fields = _extract(((item.get("result") or {}).get("message") or ""))
        lines.append(
            f"| {index} | `{item.get('event_id')}` | {item.get('title')} | "
            f"`{item.get('status')}` | {item.get('sent_at') or '-'} | "
            f"{((item.get('result') or {}).get('completed_at') or '-')} | "
            f"`{fields.get('commit', '-')}` | {fields.get('tests', '-')} |")
    lines += ["", "## 原始回读", ""]
    for item in state.get("items", []):
        result = item.get("result") or {}
        if not result.get("message"):
            continue
        lines += [f"### `{item.get('event_id')}`", "", "```text",
                  str(result["message"]).strip(), "```", ""]
    lines += [
        "## 仓库快照",
        "",
        f"- AEP `{aep.get('branch', '')}` @ `{aep.get('head', '')}`",
        f"- AEGS `{peer.get('branch', '')}` @ `{peer.get('head', '')}`",
        "",
        "### AEP 最近提交",
        "```text", aep.get("log", ""), "```",
        "",
        "### AEGS 最近提交",
        "```text", peer.get("log", ""), "```",
        "",
        "### 工作区状态（未提交内容必须人工处置，不得自动回滚）",
        "```text",
        "AEP:", aep.get("status", "") or "(clean)",
        "AEGS:", peer.get("status", "") or "(clean)",
        "```",
        "",
        "## 证据边界",
        "",
        "- `task_delivered=true` 只代表事件 ID 从同一会话回读，等于 `MESSAGE_VISIBLE`；不是 ACK。",
        "- 每轮结果以 Codex 本地 rollout 的 `task_complete.last_agent_message` 为准，属于独立 `TASK_RESULT_READBACK` 通道。",
        "- 出现结果不明或超时，本运行只 HOLD，不重发、不切换服务、不新开会话。",
        "- AEGS 的 PASS 只是项目证据，不是 AEP 授权、claim/lease 或任务完成判定。",
        "",
        "## 回滚",
        "",
        "1. `schtasks /Delete /TN \"AEP-Nightly-Codex\" /F` 停止后续 tick。",
        "2. 将 `coordination/nightly_loop/state.json` 的 `stopped` 置为 `true`（或直接停用计划任务）。",
        "3. `coordination/ui_profiles/chatgpt.json` 的原始备份见同目录时间戳备份。",
        "4. 不执行 `git reset`/`force-push`；未提交改动保留并人工复核。",
    ]
    report = "\n".join(lines).rstrip() + "\n"
    target["report"].parent.mkdir(parents=True, exist_ok=True)
    target["report"].write_text(report, encoding="utf-8")
    status = {
        "schema": SCHEMA,
        "run_id": state["run_id"],
        "status": state["status"],
        "finished": state.get("finished", False),
        "stopped": state.get("stopped", False),
        "cursor": state.get("cursor", 0),
        "send_count": state.get("send_count", 0),
        "bound_thread_id": state["bound_thread_id"],
        "fingerprint": state.get("fingerprint", ""),
        "aep_head": aep.get("head", ""),
        "aegs_head": peer.get("head", ""),
        "pending": next((item for item in state.get("items", [])
                         if item.get("status") not in {"result"}), None),
        "report": str(target["report"]),
        "updated_at": _iso(now),
    }
    _write_json(target["status"], status)
    state["report"] = str(target["report"])
    return status


def _stop(state: dict[str, Any], status: str, reason: str, now: datetime) -> None:
    state["status"] = status
    state["reason"] = reason
    state["finished"] = status not in {"AWAITING_RESULT", "SENT", "READY"}
    state["updated_at"] = _iso(now)


def tick(root: Path, config_path: Path, *, now: datetime | None = None,
         adapter: Any | None = None) -> dict[str, Any]:
    now = now or _now()
    config = load_config(config_path)
    target = paths(root)
    with _tick_lock(target["lock"]) as locked:
        if not locked:
            return {"schema": SCHEMA, "status": "TICK_BUSY",
                    "note": "another nightly loop tick holds the lock"}
        state = load_state(root, config, now)
        if not state.get("started_at"):
            state["started_at"] = _iso(now)
            state["deadline"] = _iso(now + timedelta(hours=float(config.get("run_hours", 8))))
        adapter = adapter or SystemAdapter(root, config)
        deadline = _parse(state["deadline"])
        if state.get("stopped"):
            _stop(state, "STOPPED", "operator stopped the run", now)
            _write_json(target["state"], state)
            return _finalize_report(root, state, adapter, now)
        if state.get("finished"):
            return _finalize_report(root, state, adapter, now)
        if deadline and now >= deadline:
            _stop(state, "FINISHED_DEADLINE", "8h window elapsed", now)
            _write_json(target["state"], state)
            return _finalize_report(root, state, adapter, now)

        items = state.get("items", [])
        cursor = int(state.get("cursor", 0))
        if cursor >= len(items):
            _stop(state, "FINISHED_QUEUE", "task queue exhausted", now)
            _write_json(target["state"], state)
            return _finalize_report(root, state, adapter, now)
        item = items[cursor]
        status = item.get("status")
        grace = float(config.get("result_grace_seconds", DEFAULT_RESULT_GRACE_SECONDS))
        if status in {"uncertain", "hold"}:
            _stop(state, "HOLD_UNCERTAIN_DELIVERY",
                  "previous delivery is uncertain or held; no retry", now)
            _write_json(target["state"], state)
            return _finalize_report(root, state, adapter, now)

        if status in {"sent", "awaiting"}:
            result = adapter.result(state["bound_thread_id"], item["marker"],
                                    item.get("sent_at") or state["started_at"])
            if result.get("status") == codex_rollout.SEEN and result.get("task_result_readback") is True:
                item["status"] = "result"
                item["result"] = {
                    "status": result.get("status"),
                    "turn_id": result.get("turn_id", ""),
                    "completed_at": result.get("completed_at", ""),
                    "message": result.get("message", ""),
                    "rollout_sha256": result.get("rollout_sha256", ""),
                }
                item["fields"] = _extract(result.get("message", ""))
                state["fingerprint"] = item["event_id"]
                state["cursor"] = cursor + 1
                _record(state, "RESULT_READBACK", {
                    "event_id": item["event_id"],
                    "turn_id": result.get("turn_id", ""),
                    "completed_at": result.get("completed_at", ""),
                }, now)
                if state["cursor"] >= len(items):
                    _stop(state, "FINISHED_QUEUE", "all tasks produced results", now)
                else:
                    state["status"] = "RESULT_RECORDED"
                    state["reason"] = ""
                    state["finished"] = False
                _write_json(target["state"], state)
                return _finalize_report(root, state, adapter, now)
            if result.get("status") in {codex_rollout.UNREADABLE, codex_rollout.NO_THREAD}:
                _stop(state, "HOLD_RESULT_UNREADABLE",
                      f"rollout read failed: {result.get('reason', result.get('status'))}", now)
                _record(state, "RESULT_HOLD", {"event_id": item["event_id"],
                                               "result": result}, now)
                _write_json(target["state"], state)
                return _finalize_report(root, state, adapter, now)
            sent_at = _parse(item.get("sent_at"))
            age = (now - sent_at).total_seconds() if sent_at else 0
            if age >= grace:
                _stop(state, "HOLD_RESULT_TIMEOUT",
                      f"no result marker within {int(grace)}s; not resending", now)
                _record(state, "RESULT_TIMEOUT", {"event_id": item["event_id"],
                                                  "age_seconds": age}, now)
                _write_json(target["state"], state)
                return _finalize_report(root, state, adapter, now)
            state["status"] = "AWAITING_RESULT"
            state["reason"] = f"waiting for turn result ({int(age)}s/{int(grace)}s)"
            state["finished"] = False
            _record(state, "RESULT_WAIT", {"event_id": item["event_id"],
                                           "age_seconds": age}, now)
            _write_json(target["state"], state)
            return _finalize_report(root, state, adapter, now)

        if int(state.get("send_count", 0)) >= int(config.get("max_sends", DEFAULT_MAX_SENDS)):
            _stop(state, "HOLD_SEND_BUDGET", "configured send budget exhausted", now)
            _write_json(target["state"], state)
            return _finalize_report(root, state, adapter, now)

        ok, evidence = _preflight(state, adapter)
        if not ok:
            state["status"] = "HOLD_PREFLIGHT"
            state["reason"] = evidence.get("reason", "preflight failed")
            state["finished"] = False
            _record(state, "PREFLIGHT_HOLD", evidence, now)
            _write_json(target["state"], state)
            return _finalize_report(root, state, adapter, now)

        text = (str(item["prompt"]).replace("{event_id}", item["event_id"])
                .replace("{marker}", item["marker"]))
        if item["event_id"] not in text or item["marker"] not in text:
            raise ValueError("task prompt must contain {event_id} and {marker} placeholders")
        receipt = adapter.deliver(item["event_id"], text)
        report = _report_of(receipt)
        delivered = report.get("task_delivered") is True
        item["receipt"] = receipt
        item["sent_at"] = _iso(now)
        if delivered:
            item["status"] = "sent"
            state["send_count"] = int(state.get("send_count", 0)) + 1
            state["status"] = "SENT"
            state["reason"] = "event id read back from the bound view; awaiting rollout result"
            state["finished"] = False
            _record(state, "DELIVER", {"event_id": item["event_id"],
                                       "status": report.get("status"),
                                       "task_delivered": True}, now)
            _write_json(target["state"], state)
            return _finalize_report(root, state, adapter, now)
        status_text = str(report.get("status") or "NO_RECEIPT").strip()
        if status_text.lower() in PROVEN_NOT_SENT:
            item["status"] = "queued"
            state["status"] = "RETRYABLE_NOT_SENT"
            state["reason"] = f"adapter proved no input: {status_text}"
            state["finished"] = False
            _record(state, "DELIVER_NOT_SENT", {"event_id": item["event_id"],
                                                "status": status_text}, now)
            _write_json(target["state"], state)
            return _finalize_report(root, state, adapter, now)
        item["status"] = "uncertain"
        _stop(state, "HOLD_UNCERTAIN_DELIVERY",
              f"delivery outcome is uncertain ({status_text}); no retry", now)
        _record(state, "DELIVER_UNCERTAIN", {"event_id": item["event_id"],
                                             "status": status_text}, now)
        _write_json(target["state"], state)
        return _finalize_report(root, state, adapter, now)


def status(root: Path, config_path: Path, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    config = load_config(config_path)
    state = load_state(root, config, now)
    items = state.get("items", [])
    return {
        "schema": SCHEMA,
        "run_id": state.get("run_id"),
        "status": state.get("status"),
        "finished": state.get("finished", False),
        "stopped": state.get("stopped", False),
        "started_at": state.get("started_at"),
        "deadline": state.get("deadline"),
        "cursor": state.get("cursor", 0),
        "send_count": state.get("send_count", 0),
        "fingerprint": state.get("fingerprint"),
        "items": [{"task_id": item.get("task_id"), "event_id": item.get("event_id"),
                   "title": item.get("title"), "status": item.get("status"),
                   "sent_at": item.get("sent_at"),
                   "completed_at": (item.get("result") or {}).get("completed_at", "")}
                  for item in items],
        "report": state.get("report", str(paths(root)["report"])),
        "updated_at": state.get("updated_at"),
    }


def stop(root: Path, config_path: Path, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    config = load_config(config_path)
    state = load_state(root, config, now)
    state["stopped"] = True
    _write_json(paths(root)["state"], state)
    return status(root, config_path, now=now)
