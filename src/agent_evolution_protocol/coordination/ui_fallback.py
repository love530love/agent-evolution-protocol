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

from agent_evolution_protocol.coordination import a11y
from agent_evolution_protocol.coordination import input_routing
from agent_evolution_protocol.coordination import uia_argv
from agent_evolution_protocol.coordination import vision_grounding
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import sys
from typing import Protocol
from uuid import uuid4


AGENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
EVENT = re.compile(r"^[A-Za-z0-9_-]{4,96}$")
BASE = Path(__file__).resolve().parent
UIA = BASE / "wake_adapters" / "uia_session.ps1"
SELECT_SESSION = BASE / "wake_adapters" / "uia_select_session.ps1"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


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
    hints = profile.get("composer_hints", [])
    if hints is None:
        hints = []
    if not isinstance(hints, list) or any(not isinstance(item, str) or not item.strip() for item in hints):
        raise ValueError("composer_hints must be a list of non-empty strings")
    profile["composer_hints"] = [item.strip() for item in hints]
    busy_markers = profile.get("busy_markers", [])
    if busy_markers is None:
        busy_markers = []
    if not isinstance(busy_markers, list) or any(not isinstance(item, str) or not item.strip() for item in busy_markers):
        raise ValueError("busy_markers must be a list of non-empty strings")
    profile["busy_markers"] = [item.strip() for item in busy_markers]
    profile["require_activity_ready"] = bool(profile.get("require_activity_ready", False))
    send_mode = str(profile.get("send_mode", "enter")).strip().lower()
    if send_mode not in {"enter", "button", "auto"}:
        raise ValueError("send_mode must be enter, button or auto")
    profile["send_mode"] = send_mode
    interject_hints = profile.get("interject_hints", [])
    if interject_hints is None:
        interject_hints = []
    if not isinstance(interject_hints, list) or any(
        not isinstance(item, str) or not item.strip() for item in interject_hints
    ):
        raise ValueError("interject_hints must be a list of non-empty strings")
    profile["interject_hints"] = [item.strip() for item in interject_hints]
    profile["allow_busy_interject"] = bool(profile.get("allow_busy_interject", False))
    for key in ("session_evidence_markers", "model_evidence_markers"):
        markers = profile.get(key, [])
        if markers is None:
            markers = []
        if not isinstance(markers, list) or any(not isinstance(item, str) or not item.strip()
                                                for item in markers):
            raise ValueError(f"{key} must be a list of non-empty strings")
        profile[key] = [item.strip() for item in markers]
    ocr_language = str(profile.get("ocr_language", "") or "").strip()
    if ocr_language and not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}", ocr_language):
        raise ValueError("ocr_language must be a BCP-47 language tag")
    profile["ocr_language"] = ocr_language
    return profile


def _evidence_present(text: str, exact: str, markers: list[str]) -> bool:
    """Verify a session/model even when OCR mangles the primary label.

    Electron clients often render the active chat title outside the OCR region
    or with CJK glyph loss.  A profiled client may provide stable, unique
    content markers from the same conversation; all markers must be present so
    the fallback never degrades into a workspace-only guess.
    """
    if exact and exact in text:
        return True
    if not markers:
        return False
    folded = (text or "").casefold()
    return all(marker.casefold() in folded for marker in markers)


class Driver(Protocol):
    def observe(self, profile: dict) -> dict: ...
    def capture(self, profile: dict) -> str: ...
    def select_session(self, profile: dict) -> dict: ...
    def launch(self, profile: dict) -> None: ...
    def deliver(self, profile: dict, message: str, event_id: str, *, urgent: bool = False,
                composer_ratios: tuple[float, float] | None = None) -> dict: ...


def annotate_elevation(report: dict) -> dict:
    """Say up front when Windows will refuse to drive this window.

    Running the clients elevated is a legitimate choice -- an unelevated agent
    process that needs admin rights gets stuck on UAC.  The consequence is that
    the executor must be elevated too: a medium-integrity process cannot
    enumerate, move or type into a higher-integrity window, and every one of
    those refusals is silent.  Without this annotation the caller sees
    ``input_found: false`` and no reason for it.
    """
    if not report or not a11y.available():
        return report
    pid = report.get("pid")
    if not pid:
        return report
    target = a11y.elevation(int(pid))
    executor = a11y.self_elevation()
    report["target_elevation"] = target
    report["executor_elevation"] = executor
    ok, reason = a11y.drivability(target, executor)
    report["drivable"] = ok
    if reason == "TARGET_NEEDS_ELEVATION":
        report["blocked_by"] = reason
        report["hint"] = (f"target runs {target}, executor runs {executor}; UIPI is one-way, "
                          "so the executor must be elevated at least as high as the client")
    elif reason:
        report["blocked_by"] = reason
        report["hint"] = "the executor's own integrity level could not be read"
    return report


def drivability_block(observation: dict) -> dict:
    """Return the deferral fields when this executor provably cannot drive.

    ``annotate_elevation`` already puts the verdict on the observation, but
    nothing acted on it: an unelevated executor pointed at an elevated client
    spent the full 75-second UI attempt, then recorded ``CEASED`` with
    ``SUBMISSION_UNCONFIRMED: possible send``.  That note is the worst of both
    worlds -- it costs a minute, and it claims a send *may* have happened when
    UIPI guarantees that nothing was typed at all.

    An empty dict means "no objection", including on hosts where the check is
    unavailable, so this can never become a new way to block delivery.
    """
    if not observation or observation.get("drivable") is not False:
        return {}
    blocked = {"drivable": False,
               "target_elevation": observation.get("target_elevation"),
               "executor_elevation": observation.get("executor_elevation")}
    if observation.get("blocked_by"):
        blocked["blocked_by"] = observation["blocked_by"]
    return blocked


def profile_args(profile: dict) -> list[str]:
    """Adapter flags implied by a UI profile.

    One copy, two callers.  ``PowerShellUiaDriver`` builds them for the
    scheduled path and ``coord-uia`` builds them for the operator's command;
    when the two had their own lists, the command kept naming a Qoder version
    that had been replaced and passed no ``-AllowUnknownPath``, so it could not
    find a window the scheduler could.
    """
    args: list[str] = []
    hints = [str(hint) for hint in profile.get("interject_hints", []) if str(hint).strip()]
    if hints:
        args += ["-InterjectHints", ",".join(hints)]
    # Without this the recognizer silently falls back to the user profile
    # (en-US here), which turns a Chinese UI into Latin look-alikes.
    if profile.get("ocr_language"):
        args += ["-OcrLanguage", str(profile["ocr_language"])]
    if profile.get("allow_unknown_path"):
        args.append("-AllowUnknownPath")
    strict_ocr = profile.get("transport") == "ocr" and profile.get("strict_composer_hint", False)
    if profile.get("composer_hint") and not strict_ocr:
        args += ["-ComposerHint", str(profile["composer_hint"])]
    # PowerShell binds a string[] parameter from one comma-separated argument.
    # Repeating the parameter name causes a terminating
    # ParameterBindingException before the UI script can focus the composer,
    # which is a provably pre-input failure.
    composer_hints = [str(hint) for hint in profile.get("composer_hints", []) if str(hint).strip()]
    if composer_hints and not strict_ocr:
        args += ["-ComposerHints", ",".join(composer_hints)]
    if strict_ocr:
        args += ["-RequireComposerHint", "-ComposerHint", str(profile["composer_hint"])]
        if profile.get("allow_composer_geometry_fallback"):
            args.append("-AllowComposerGeometryFallback")
    if profile.get("send_button_x_ratio") and profile.get("send_button_y_ratio"):
        args += ["-SendButtonXRatio", str(profile["send_button_x_ratio"]),
                 "-SendButtonYRatio", str(profile["send_button_y_ratio"])]
    if profile.get("composer_x_ratio") and profile.get("composer_y_ratio"):
        args += ["-ComposerXRatio", str(profile["composer_x_ratio"]),
                 "-ComposerYRatio", str(profile["composer_y_ratio"])]
    # The adapter refuses to paste into a client it could not read unless this
    # is passed, and Python never passed it -- so an opaque client reached
    # NO_INPUT_CONTROL with no way out.  Kept per-profile and opt-in because
    # the paste lands wherever activation put focus, which is why the
    # adapter's own message says "only with human supervision".
    if profile.get("allow_clipboard_paste"):
        args.append("-AllowClipboardPaste")
    return args


class PowerShellUiaDriver:
    """Native UIA first; exact OCR composer hint only for opaque clients."""

    def __init__(self):
        self.pwsh = shutil.which("pwsh.exe")
        if not self.pwsh:
            raise RelayError("PowerShell 7 (pwsh.exe) is required")

    @staticmethod
    def _ui_env(profile: dict) -> dict[str, str]:
        """Pass opt-in visual recovery flags without changing the host shell."""
        env = os.environ.copy()
        # Keep coordinates in the physical desktop space used by screenshots
        # unless a profile was explicitly calibrated in a DPI-aware process.
        if profile.get("dpi_aware"):
            env["AEP_DPI_AWARE"] = "1"
        else:
            env.pop("AEP_DPI_AWARE", None)
        if profile.get("scroll_composer"):
            env["AEP_SCROLL_COMPOSER"] = "1"
        if profile.get("maximize_window"):
            env["AEP_MAXIMIZE_WINDOW"] = "1"
        if profile.get("allow_minor_clipping"):
            env["AEP_ALLOW_MINOR_CLIPPING"] = "1"
        if profile.get("send_button_color"):
            color = str(profile["send_button_color"])
            # Keep the historical purple sentinel for existing profiles while
            # allowing additional client-specific color probes.
            env["AEP_SEND_BUTTON_COLOR"] = "1" if color == "purple" else color
        return env

    def _run(self, script: Path, args: list[str], timeout: int = 45,
             env: dict[str, str] | None = None) -> dict:
        completed = uia_argv.run([self.pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass",
                                  "-File", str(script), *args],
                                 timeout=timeout, env=env)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout)[-1200:]
            raise RelayError(f"UI_SCRIPT_FAILED:{detail}")
        for line in reversed((completed.stdout or "").splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise RelayError("UI_SCRIPT_NO_JSON_RECEIPT")

    @staticmethod
    def _target(profile: dict) -> list[str]:
        return ["-Agent", profile["agent"], "-ProcessName", profile["process"],
                "-ExpectedTitle", profile["title"], "-ExpectedPath", profile["exe"],
                *profile_args(profile)]

    def observe(self, profile: dict) -> dict:
        try:
            args = self._target(profile) + ["-Mode", "observe"]
            if profile.get("activity_ocr"):
                args.append("-OcrVerify")
            return annotate_elevation(self._run(UIA, args, env=self._ui_env(profile)))
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
                                                    "-OutFile", str(out)], timeout=75,
                      env=self._ui_env(profile))
            return out.read_text(encoding="utf-8")

    def select_session(self, profile: dict) -> dict:
        if profile["transport"] == "ocr":
            return self._run(UIA, self._target(profile) + ["-Mode", "select-ocr",
                                                         "-SessionLabel", profile["session_marker"]], timeout=75,
                              env=self._ui_env(profile))
        args = self._target(profile) + ["-SessionLabel", profile["session_marker"]]
        return self._run(SELECT_SESSION, args)

    def launch(self, profile: dict) -> None:
        executable = Path(profile["exe"])
        if not executable.is_file():
            raise RelayError("EXECUTABLE_MISSING")
        subprocess.Popen([str(executable)], cwd=str(executable.parent), close_fds=True)

    def deliver(self, profile: dict, message: str, event_id: str, *, urgent: bool = False,
                composer_ratios: tuple[float, float] | None = None) -> dict:
        args = self._target(profile) + [
            "-Mode", "compose" if profile["transport"] == "ocr" else "deliver",
            "-Text", message, "-EventId", event_id, "-ReadBackTimeoutSeconds", "5",
            "-SendMode", profile.get("send_mode", "enter")]
        if composer_ratios is not None:
            # Authorised by the router, not by the profile: the bounded geometry
            # fallback only runs because something outside the adapter produced
            # a box, and the adapter's own paste verification still has to pass
            # before any send control is clicked.
            ratio_x, ratio_y = composer_ratios
            args += ["-AllowComposerGeometryFallback",
                     "-ComposerXRatio", f"{ratio_x}", "-ComposerYRatio", f"{ratio_y}"]
        if urgent:
            args.append("-UrgentInterject")
        return annotate_elevation(self._run(UIA, args, timeout=75, env=self._ui_env(profile)))


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


def _preinput_receipt_retryable(existing: dict) -> bool:
    """Recognize only receipts that prove the UI was never touched."""
    if not isinstance(existing, dict) or existing.get("task_delivered", False):
        return False
    if existing.get("state") in {
        "NO_WINDOW", "AGENT_BUSY", "ACTIVITY_UNCONFIRMED",
        "URGENT_INTERJECT_UNAVAILABLE",
        "WINDOW_OR_INPUT_AMBIGUOUS", "NO_SAFE_COMPOSER",
        "SESSION_NOT_VERIFIED", "MODEL_NOT_VERIFIED",
        "EXECUTOR_TOO_LOW", "ROUTE_REGION_UNUSABLE", "ROUTE_UNSUPPORTED_BY_DRIVER",
    }:
        return True
    if existing.get("state") != "SEND_UNCERTAIN":
        return False
    report = existing.get("send_report") or {}
    if (report.get("status") == "composer-not-confirmed" and
            not report.get("task_delivered", False)):
        return True
    error = str(existing.get("error", ""))
    return ("COMPOSER_HINT_" in error or
            ("ComposerHints" in error and
             ("绑定参数" in error or "指定了参数" in error or
              "ParameterBindingException" in error)))


def _deliver(driver: Driver, profile: dict, message: str, event_id: str, *, urgent: bool,
             composer_ratios: tuple[float, float] | None = None) -> dict:
    """Call both new and legacy drivers without turning a legacy driver into a
    false urgent success.  Normal dispatch remains compatible with older
    adapters; urgent interjection requires an adapter that explicitly accepts
    the urgent keyword.

    The same rule covers a located composer: an adapter that cannot take a
    region must say so rather than drop it, because a route that is computed and
    then ignored looks exactly like a route that was never needed.
    """
    import inspect

    try:
        parameters = inspect.signature(driver.deliver).parameters
    except (TypeError, ValueError):
        parameters = {}
    supports_urgent = "urgent" in parameters
    supports_region = "composer_ratios" in parameters
    if urgent and not supports_urgent:
        return {"status": "urgent-interject-unsupported", "interruption": False}
    if composer_ratios is not None and not supports_region:
        return {"status": "composer-region-unsupported", "interruption": False}
    kwargs: dict = {}
    if supports_urgent:
        kwargs["urgent"] = urgent
    if supports_region and composer_ratios is not None:
        kwargs["composer_ratios"] = composer_ratios
    return driver.deliver(profile, message, event_id, **kwargs)


def _window_from(observation: dict) -> vision_grounding.Region | None:
    """The window box the observation was taken against, if it reported one.

    A router cannot convert a 0-999 answer without a denominator, and a ratio
    cannot be computed without a size, so an observation from an older executor
    routes as "no geometry" instead of guessing a resolution.
    """
    size = observation.get("window_size") or {}
    try:
        width, height = int(size["width"]), int(size["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return vision_grounding.Region(x=0, y=0, width=width, height=height)


def _composer_ratios(region: dict | None,
                     window: vision_grounding.Region | None) -> tuple[float, float] | None:
    """Turn a window-relative box into the centre ratios the adapter accepts."""
    if not region or window is None:
        return None
    try:
        x, y = int(region["x"]), int(region["y"])
        width, height = int(region["width"]), int(region["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    ratio_x = (x + width / 2) / window.width
    ratio_y = (y + height / 2) / window.height
    if not (0 < ratio_x < 1 and 0 < ratio_y < 1):
        return None
    return round(ratio_x, 4), round(ratio_y, 4)


def run(profile: dict, message: str, *, event_id: str, receipt_path: Path,
        lock_path: Path, driver: Driver, allow_launch: bool = False,
        poll_seconds: float = 1.5, urgent: bool = False,
        grounder=None, reference_region: dict | None = None,
        min_confidence: float = vision_grounding.DEFAULT_MIN_CONFIDENCE) -> dict:
    if not EVENT.fullmatch(event_id) or not message.strip():
        raise ValueError("event_id and message required")
    if poll_seconds < 0.1:
        raise ValueError("poll interval too short")
    receipt_path = Path(receipt_path)
    if receipt_path.exists():
        existing = json.loads(receipt_path.read_text(encoding="utf-8"))
        # A pre-input readiness result is deliberately retryable after the
        # target becomes ready.  No composer write or send control was
        # attempted in these states, so reusing the same event ID cannot
        # duplicate a user-visible message.  All post-input/uncertain states
        # remain terminal and retain the original receipt for auditability.
        if not _preinput_receipt_retryable(existing):
            return existing
    with _desktop_lock(Path(lock_path)):
        if receipt_path.exists():
            existing = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not _preinput_receipt_retryable(existing):
                return existing
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
        # Refuse before spending the attempt, not after.  This runs before the
        # busy/readiness checks on purpose: when the executor cannot drive the
        # window at all, "busy, wait for ready" is an answer that can never
        # become true, and a supervisor polling on it would wait forever.
        blocked = drivability_block(observation)
        if blocked:
            state.update(state="EXECUTOR_TOO_LOW",
                         pid=observation.get("pid"),
                         reason="UIPI refuses input from a lower integrity level, so nothing was typed",
                         hint=observation.get("hint", ""), **blocked)
            _atomic(receipt_path, state)
            return state
        # A visible window is not necessarily ready for another task.  In
        # particular, Qoder/WorkBuddy can keep the composer visible while a
        # tool run is still active.  Never type over that run: defer and let
        # the supervisor resume only after a later readiness observation.
        activity = str(observation.get("activity_state", "")).lower()
        if activity == "busy":
            interject = (observation.get("interject_evidence") or
                         (observation.get("activity_evidence") or {}).get("interject_evidence") or {})
            if not urgent:
                state.update(state="AGENT_BUSY", reason="target reports an active generation/tool run",
                             activity=observation.get("activity_evidence", {}))
                _atomic(receipt_path, state)
                return state
            if not profile.get("allow_busy_interject"):
                state.update(state="URGENT_INTERJECT_UNAVAILABLE",
                             reason="profile has not enabled a non-interrupting interject action",
                             activity=observation.get("activity_evidence", {}),
                             interject=interject)
                _atomic(receipt_path, state)
                return state
            if not interject.get("ready") or interject.get("interrupt_only"):
                state.update(state="URGENT_INTERJECT_UNAVAILABLE",
                             reason="no uniquely identified non-interrupting interject control was observed",
                             activity=observation.get("activity_evidence", {}),
                             interject=interject)
                _atomic(receipt_path, state)
                return state
        if (profile.get("require_activity_ready") and
                activity not in {"ready"}):
            state.update(state="ACTIVITY_UNCONFIRMED",
                         reason="target readiness was not independently observed",
                         activity=observation.get("activity_evidence", {}))
            _atomic(receipt_path, state)
            return state
        pid = observation.get("pid")
        if not pid:
            state.update(state="WINDOW_OR_INPUT_AMBIGUOUS", route_reason="NO_PID")
            _atomic(receipt_path, state)
            return state
        # Which leg located the input, and what it would take to use it.  This
        # runs for every attempt, including the ones UIA settles, because a
        # receipt that only records the interesting cases cannot answer "did we
        # ask a model here?" after the fact.
        window = _window_from(observation)
        route = input_routing.resolve_target(
            observation, window=window, grounder=grounder,
            reference=vision_grounding.Region(**reference_region) if reference_region else None,
            min_confidence=min_confidence)
        state["route"] = route
        if profile["transport"] == "uia" and route["locate_source"] != "uia":
            # The uia transport types through a control UIA itself identified.
            # A region from a model or a profile cannot be handed to
            # ValuePattern, and the only alternative the adapter offers is a
            # blind paste into whatever has focus -- which is why the located
            # box is recorded here instead of acted on.
            ambiguous = bool(observation.get("input_ambiguous")) or bool(observation.get("input_found"))
            state.update(state="WINDOW_OR_INPUT_AMBIGUOUS" if ambiguous else "NO_SAFE_COMPOSER",
                         located_by=route["locate_source"],
                         located_region=route["region"],
                         route_reason=route["reason"] or "TRANSPORT_CANNOT_USE_LOCATED_REGION")
            _atomic(receipt_path, state)
            return state
        state["pid"] = pid
        before = driver.capture(profile)
        if not _evidence_present(before, profile["session_marker"],
                                 profile.get("session_evidence_markers", [])):
            selected = driver.select_session(profile)
            state["session_selection"] = selected
            before = driver.capture(profile)
        if not _evidence_present(before, profile["session_marker"],
                                 profile.get("session_evidence_markers", [])):
            # Record what was looked for.  A profile whose marker does not match
            # the live window is indistinguishable from "the session is gone"
            # unless the receipt names the marker, and a one-character typo in
            # a local profile then stalls every tick forever.
            state.update(state="SESSION_NOT_VERIFIED",
                         session_marker=profile["session_marker"],
                         session_evidence_markers=list(profile.get("session_evidence_markers", [])),
                         session_marker_present=bool(profile["session_marker"] in before),
                         captured_chars=len(before))
            _atomic(receipt_path, state)
            return state
        if not _evidence_present(before, profile["model_marker"],
                                 profile.get("model_evidence_markers", [])):
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
        if urgent:
            outgoing = ("【紧急插话｜保持当前任务继续运行】\n" + outgoing +
                        "\n这是非中断插话；不要点击停止、取消或中止。")
        state.update(state="SEND_ATTEMPTED", session_marker=profile["session_marker"],
                     model_marker=profile["model_marker"], challenge=nonce,
                     expected_ack=expected_ack, outgoing_chars=len(outgoing))
        # A located region is only usable where the transport can act on one.
        # The OCR compose path accepts a bounded, profiled geometry fallback and
        # still verifies the paste before clicking send; the uia path has no
        # such mode, which is why it deferred above instead of pasting blind.
        composer_ratios = None
        if route["locate_source"] in {"vision", "geometry"}:
            composer_ratios = _composer_ratios(route.get("region"), window)
            if composer_ratios is None:
                state.update(state="ROUTE_REGION_UNUSABLE",
                             located_by=route["locate_source"],
                             located_region=route.get("region"),
                             route_reason="NO_USABLE_RATIO")
                _atomic(receipt_path, state)
                return state
            state["composer_ratios"] = list(composer_ratios)
            state["located_by"] = route["locate_source"]
        # Persist before touching the UI: after a crash, a retry may not send again.
        _atomic(receipt_path, state)
        try:
            sent = _deliver(driver, profile, outgoing, event_id, urgent=urgent,
                            composer_ratios=composer_ratios)
        except Exception as exc:  # noqa: BLE001
            state.update(state="SEND_UNCERTAIN", error=str(exc)[:800])
            _atomic(receipt_path, state)
            return state
        state["send_report"] = sent
        if sent.get("status") == "composer-region-unsupported":
            # The router located the composer and the driver cannot use it.
            # Nothing was typed, so this is a retryable deferral rather than an
            # uncertain send -- but it is not a success either.
            state.update(state="ROUTE_UNSUPPORTED_BY_DRIVER",
                         reason="the driver cannot type into a located region; nothing was sent",
                         located_by=route["locate_source"])
            _atomic(receipt_path, state)
            return state
        accepted_statuses = {"message-visible", "submitted-unconfirmed"}
        if urgent:
            accepted_statuses |= {"urgent-interject-visible", "urgent-interject-submitted"}
            if sent.get("interruption", False) or not sent.get("interject_action"):
                state.update(state="URGENT_INTERJECT_UNCERTAIN",
                             reason="send receipt did not prove a non-interrupting interject action",
                             send_report=sent)
                _atomic(receipt_path, state)
                return state
        if sent.get("status") not in accepted_statuses:
            state.update(state="SEND_UNCERTAIN")
            _atomic(receipt_path, state)
            return state
        # A visible message is delivered whether or not the reply arrives.
        # Leaving task_delivered at its initial False made a delivered message
        # read as undelivered, which is the same false negative that invites a
        # duplicate send.  Acceptance stays separate: only the target's own
        # token sets agent_accepted.
        state["task_delivered"] = sent.get("task_delivered") is True
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
            if not _evidence_present(text, profile["session_marker"],
                                     profile.get("session_evidence_markers", [])):
                state.update(state="SESSION_CHANGED")
                break
            if not _evidence_present(text, profile["model_marker"],
                                     profile.get("model_evidence_markers", [])):
                state.update(state="MODEL_CHANGED")
                break
            if expected_ack in text:
                state.update(state="AGENT_REPLY_VERIFIED", task_delivered=True,
                             agent_accepted=True, reply_excerpt=text[text.index(expected_ack):][-4000:],
                             replied_at=_utc())
                break
        else:
            # The old name was REPLY_TIMEOUT_UNCERTAIN, which mixed up two
            # claims: whether the message arrived and whether the target
            # answered.  The state now names only the certain fact -- no reply
            # arrived in time -- and leaves the delivery claim to
            # task_delivered, which the send receipt already proved.
            state.update(state="NO_REPLY_WITHIN_TIMEOUT")
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
    parser.add_argument("--urgent-interject", action="store_true",
                        help="use a profiled non-interrupting interject action while the agent is busy")
    parser.add_argument("--route-endpoint",
                        help="grounding service base URL; the router only consults it when UIA is not decisive")
    parser.add_argument("--route-provider", default="mage_vl_cowork")
    parser.add_argument("--route-reference",
                        help="x,y,width,height of a configured composer region, used only as the last resort")
    parser.add_argument("--route-min-confidence", type=float,
                        default=vision_grounding.DEFAULT_MIN_CONFIDENCE)
    args = parser.parse_args()
    try:
        profile = load_profile(args.profile)
        message = args.message_file.read_text(encoding="utf-8")
        grounder = None
        if args.route_endpoint:
            grounder = vision_grounding.HttpGrounder(args.route_endpoint,
                                                     provider_id=args.route_provider)
        reference = None
        if args.route_reference:
            parts = [int(part) for part in args.route_reference.split(",")]
            if len(parts) != 4:
                raise ValueError("--route-reference needs x,y,width,height")
            reference = dict(zip(("x", "y", "width", "height"), parts))
        output = run(profile, message, event_id=args.event_id, receipt_path=args.receipt,
                     lock_path=args.lock, driver=PowerShellUiaDriver(), allow_launch=args.allow_launch,
                     urgent=args.urgent_interject, grounder=grounder,
                     reference_region=reference,
                     min_confidence=args.route_min_confidence)
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
