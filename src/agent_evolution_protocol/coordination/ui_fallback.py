"""Bounded human-style desktop relay for an explicitly profiled agent session.

The relay does not infer a model's answer from window text alone. It uses a
challenge/response token that is absent from the outgoing prompt, so the
outgoing message cannot acknowledge itself. Uncertain writes are never retried.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from typing import Protocol
from uuid import uuid4


AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
EVENT = re.compile(r"^[A-Za-z0-9_-]{4,96}$")
BASE = Path(__file__).resolve().parent
UIA = BASE / "wake_adapters" / "uia_session.ps1"
SELECT_SESSION = BASE / "wake_adapters" / "uia_select_session.ps1"


class RelayError(RuntimeError):
    pass


def _atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_profile(path: Path) -> dict:
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    required = ("agent", "process", "title", "exe", "session_marker", "model_marker", "transport")
    if not isinstance(profile, dict) or any(not str(profile.get(key, "")).strip() for key in required):
        raise ValueError("profile requires agent, process, title, exe, session_marker, model_marker and transport")
    if not AGENT.fullmatch(profile["agent"]):
        raise ValueError("invalid agent")
    if profile["transport"] not in {"uia", "ocr"}:
        raise ValueError("transport must be uia or ocr")
    if profile["transport"] == "ocr" and not str(profile.get("composer_hint", "")).strip():
        raise ValueError("OCR transport requires an exact composer_hint")
    if not Path(profile["exe"]).is_absolute():
        raise ValueError("exe must be an absolute path")
    profile["reply_timeout_seconds"] = int(profile.get("reply_timeout_seconds", 120))
    if not 5 <= profile["reply_timeout_seconds"] <= 300:
        raise ValueError("reply timeout must be 5..300 seconds")
    return profile


class Driver(Protocol):
    def observe(self, profile: dict) -> dict: ...
    def capture(self, profile: dict) -> str: ...
    def select_session(self, profile: dict) -> dict: ...
    def launch(self, profile: dict) -> None: ...
    def deliver(self, profile: dict, message: str, event_id: str) -> dict: ...


class PowerShellUiaDriver:
    """Native UIA first; exact OCR composer hint only for opaque clients."""

    def __init__(self):
        self.pwsh = shutil.which("pwsh.exe")
        if not self.pwsh:
            raise RelayError("PowerShell 7 (pwsh.exe) is required")

    def _run(self, script: Path, args: list[str], timeout: int = 45) -> dict:
        completed = subprocess.run([self.pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass",
                                    "-File", str(script), *args], text=True, capture_output=True,
                                   timeout=timeout, check=False)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout)[-1200:]
            raise RelayError(f"UI_SCRIPT_FAILED:{detail}")
        for line in reversed(completed.stdout.splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise RelayError("UI_SCRIPT_NO_JSON_RECEIPT")

    @staticmethod
    def _target(profile: dict) -> list[str]:
        args = ["-Agent", profile["agent"], "-ProcessName", profile["process"],
                "-ExpectedTitle", profile["title"], "-ExpectedPath", profile["exe"]]
        if profile.get("allow_unknown_path"):
            args.append("-AllowUnknownPath")
        return args

    def observe(self, profile: dict) -> dict:
        try:
            return self._run(UIA, self._target(profile) + ["-Mode", "observe"])
        except RelayError as exc:
            if "LAUNCH_FORBIDDEN" in str(exc):
                return {"status": "NO_WINDOW"}
            raise

    def capture(self, profile: dict) -> str:
        # The capture may use OCR; focus the exact existing window first so an
        # occluding app cannot be mistaken for this client's conversation.
        focus = BASE / "wake_adapters" / "existing_window.ps1"
        args = ["-Agent", profile["agent"], "-ProcessName", profile["process"],
                "-ExpectedTitle", profile["title"], "-ExpectedPath", profile["exe"]]
        if profile.get("allow_unknown_path"):
            args.append("-AllowUnknownPath")
        self._run(focus, args)
        with tempfile.TemporaryDirectory(prefix="aep-ui-capture-") as folder:
            out = Path(folder) / "conversation.txt"
            self._run(UIA, self._target(profile) + ["-Mode", "capture", "-OcrVerify",
                                                    "-OutFile", str(out)], timeout=75)
            return out.read_text(encoding="utf-8")

    def select_session(self, profile: dict) -> dict:
        if profile["transport"] == "ocr":
            return self._run(UIA, self._target(profile) + ["-Mode", "select-ocr",
                                                         "-SessionLabel", profile["session_marker"]], timeout=75)
        args = self._target(profile) + ["-SessionLabel", profile["session_marker"]]
        return self._run(SELECT_SESSION, args)

    def launch(self, profile: dict) -> None:
        executable = Path(profile["exe"])
        if not executable.is_file():
            raise RelayError("EXECUTABLE_MISSING")
        subprocess.Popen([str(executable)], cwd=str(executable.parent), close_fds=True)

    def deliver(self, profile: dict, message: str, event_id: str) -> dict:
        args = self._target(profile) + ["-Mode", "compose" if profile["transport"] == "ocr" else "deliver",
                "-Text", message, "-EventId", event_id, "-ReadBackTimeoutSeconds", "5"]
        if profile["transport"] == "ocr":
            args += ["-RequireComposerHint", "-ComposerHint", profile["composer_hint"]]
        return self._run(UIA, args, timeout=75)


@contextmanager
def _desktop_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RelayError("DESKTOP_INPUT_BUSY") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RelayError("DESKTOP_INPUT_BUSY") from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _challenge() -> tuple[str, str]:
    nonce = secrets.token_hex(7).upper()
    while nonce == nonce[::-1]:
        nonce = secrets.token_hex(7).upper()
    return nonce, "AEP-ACK:" + nonce[::-1]


def run(profile: dict, message: str, *, event_id: str, receipt_path: Path,
        lock_path: Path, driver: Driver, allow_launch: bool = False,
        poll_seconds: float = 1.5) -> dict:
    if not EVENT.fullmatch(event_id) or not message.strip():
        raise ValueError("event_id and message required")
    if poll_seconds < 0.1:
        raise ValueError("poll interval too short")
    receipt_path = Path(receipt_path)
    if receipt_path.exists():
        return json.loads(receipt_path.read_text(encoding="utf-8"))
    with _desktop_lock(Path(lock_path)):
        if receipt_path.exists():
            return json.loads(receipt_path.read_text(encoding="utf-8"))
        state = {"agent": profile["agent"], "event_id": event_id, "state": "PREFLIGHT",
                 "at": _utc(), "task_delivered": False, "agent_accepted": False}
        observation = driver.observe(profile)
        if observation.get("status") == "NO_WINDOW":
            if not allow_launch:
                state.update(state="NO_WINDOW", reason="launch not authorised")
                _atomic(receipt_path, state)
                return state
            driver.launch(profile)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                time.sleep(0.5)
                observation = driver.observe(profile)
                if observation.get("status") != "NO_WINDOW":
                    break
            if observation.get("status") == "NO_WINDOW":
                state.update(state="LAUNCH_UNVERIFIED", reason="no unique window appeared")
                _atomic(receipt_path, state)
                return state
            state["launched"] = True
        pid = observation.get("pid")
        if not pid or (profile["transport"] == "uia" and observation.get("input_ambiguous")):
            state.update(state="WINDOW_OR_INPUT_AMBIGUOUS")
            _atomic(receipt_path, state)
            return state
        if profile["transport"] == "uia" and not observation.get("input_found"):
            state.update(state="NO_SAFE_COMPOSER")
            _atomic(receipt_path, state)
            return state
        state["pid"] = pid
        before = driver.capture(profile)
        if profile["session_marker"] not in before:
            selected = driver.select_session(profile)
            state["session_selection"] = selected
            before = driver.capture(profile)
        if profile["session_marker"] not in before:
            state.update(state="SESSION_NOT_VERIFIED")
            _atomic(receipt_path, state)
            return state
        if profile["model_marker"] not in before:
            state.update(state="MODEL_NOT_VERIFIED")
            _atomic(receipt_path, state)
            return state
        nonce, expected_ack = _challenge()
        if event_id in before or expected_ack in before:
            state.update(state="NONCE_OR_EVENT_ALREADY_VISIBLE")
            _atomic(receipt_path, state)
            return state
        outgoing = (f"{message.rstrip()}\n\n事件 ID：{event_id}。请在回复中单独写 AEP-ACK:"
                    f" 后紧跟校验串 {nonce} 的倒序字符，然后写你的结论和下一步。"
                    "不要在回复前切换会话或模型。")
        state.update(state="SEND_ATTEMPTED", session_marker=profile["session_marker"],
                     model_marker=profile["model_marker"], challenge=nonce,
                     expected_ack=expected_ack, outgoing_chars=len(outgoing))
        # Persist before touching the UI: after a crash, a retry may not send again.
        _atomic(receipt_path, state)
        try:
            sent = driver.deliver(profile, outgoing, event_id)
        except Exception as exc:  # noqa: BLE001
            state.update(state="SEND_UNCERTAIN", error=str(exc)[:800])
            _atomic(receipt_path, state)
            return state
        state["send_report"] = sent
        if sent.get("status") not in {"message-visible", "submitted-unconfirmed"}:
            state.update(state="SEND_UNCERTAIN")
            _atomic(receipt_path, state)
            return state
        state["state"] = "WAITING_REPLY"
        _atomic(receipt_path, state)
        deadline = time.monotonic() + profile["reply_timeout_seconds"]
        while time.monotonic() < deadline:
            time.sleep(poll_seconds)
            current = driver.observe(profile)
            if current.get("pid") != pid:
                state.update(state="SESSION_WINDOW_CHANGED")
                break
            text = driver.capture(profile)
            if profile["session_marker"] not in text:
                state.update(state="SESSION_CHANGED")
                break
            if profile["model_marker"] not in text:
                state.update(state="MODEL_CHANGED")
                break
            if expected_ack in text:
                state.update(state="AGENT_REPLY_VERIFIED", task_delivered=True,
                             agent_accepted=True, reply_excerpt=text[text.index(expected_ack):][-4000:],
                             replied_at=_utc())
                break
        else:
            state.update(state="REPLY_TIMEOUT_UNCERTAIN")
        _atomic(receipt_path, state)
        return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--message-file", type=Path, required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--lock", type=Path, default=Path.cwd() / "coordination" / "desktop-input.lock")
    parser.add_argument("--allow-launch", action="store_true")
    args = parser.parse_args()
    try:
        profile = load_profile(args.profile)
        message = args.message_file.read_text(encoding="utf-8")
        output = run(profile, message, event_id=args.event_id, receipt_path=args.receipt,
                     lock_path=args.lock, driver=PowerShellUiaDriver(), allow_launch=args.allow_launch)
        if output["state"] == "AGENT_REPLY_VERIFIED":
            from .presence import signal
            signal(args.lock.parent, agent=profile["agent"], state="online",
                   source="verified_ui_reply", session_id=profile["session_marker"])
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if output["state"] == "AGENT_REPLY_VERIFIED" else 2
    except (ValueError, OSError, RelayError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
