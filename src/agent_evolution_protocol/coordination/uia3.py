"""Desktop driver built on the raw UIA3 client (pywinauto / CUIAutomation).

Why a second UIA client
-----------------------
``uia_session.ps1`` is built on ``System.Windows.Automation``, the *managed*
UIA2 client.  For some Chromium/Electron builds that client returns two panes
where the raw ``CUIAutomation`` client returns a full tree, so the adapter has
to fall back to OCR for a window that is perfectly readable.  Measured on this
machine at the same moment, elevated executor, screen-reader flag on::

    ChatGPT      UIA3 1388 elements
    Qoder        UIA3 1220 elements
    Qoder CN     UIA3  890 elements
    WorkBuddy    UIA3    9 elements   (8 panes + title bar)
    WorkBuddy AI UIA3    9 elements

Two things follow, and both matter.  The tree really is there for some clients
and OCR is a needless lossy step for them.  And for the two WorkBuddy builds it
is *not* there for either client -- the application never enables renderer
accessibility -- so this driver must not pretend to work: it reports the tree
size it saw and lets the caller fall back.

That is why ``AutoDriver`` exists: it asks the UIA3 client what it can see and
delegates to the OCR adapter when the answer is "almost nothing".  The decision
is made per call from the tree that is actually present, not from a flag in a
profile that can silently go stale.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from agent_evolution_protocol.coordination import process_info
from agent_evolution_protocol.coordination import session_attestation

# A Chromium window without renderer accessibility exposes its window chrome
# only: eight panes and a title bar.  Forty is comfortably above that and far
# below any real page, so it separates "no tree" from "a small tree".
UIA3_MIN_ELEMENTS = 40

BUSY_PATTERNS = ("正在执行", "执行中", "生成中", "运行中", "处理中", "思考中",
                 "generating", "running", "working", "thinking")
# A stop control only exists while something is running, so its presence is
# evidence of a live run -- the opposite of what "ready" means.  Matched
# exactly rather than as a substring, because "stop" inside a longer label
# (a menu item, a stopwatch, "stoppable") is not the same claim.
BUSY_EXACT = {"stop", "stop response", "stop generating", "停止", "停止生成", "中止", "取消"}
READY_PATTERNS = ("发送", "send", "submit")

EDITABLE_TYPES = {"Edit", "Document"}


def available() -> bool:
    """True when the UIA3 client can be used at all on this host."""
    if os.name != "nt":
        return False
    try:
        import pywinauto  # noqa: F401
    except Exception:  # noqa: BLE001 - an optional dependency must never break import
        return False
    return True


def _enum_top_level(pid: int) -> list[int]:
    """Return the visible top-level handles owned by ``pid``.

    ``EnumWindows`` is used instead of a UIA desktop walk because the walk is
    both slow and, on an elevated target, silently short: it lists what the
    calling integrity level is allowed to see, which is exactly the failure
    mode this module is meant to distinguish from "the app has no tree".
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    handles: list[int] = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            handles.append(int(hwnd))
        return True

    user32.EnumWindows(proto(_callback), 0)
    return handles


def _window_area(handle: int) -> int:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(handle), ctypes.byref(rect)):
        return 0
    return max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)


def _pids_for(profile: dict) -> list[int]:
    """Pids whose executable or image name matches the profile."""
    import psutil

    wanted_exe = str(profile.get("exe") or "").strip()
    allowed_roots = profile.get("allowed_exe_roots") or []
    wanted_name = str(profile.get("process") or "").strip().casefold()
    if wanted_name and not wanted_name.endswith(".exe"):
        wanted_name += ".exe"
    found: list[int] = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        info = proc.info
        exe = str(info.get("exe") or "")
        name = str(info.get("name") or "").casefold()
        if wanted_exe and exe and process_info.executable_match(
                wanted_exe, exe, allowed_roots=allowed_roots).get("match"):
            found.append(int(info["pid"]))
        elif not wanted_exe and wanted_name and name == wanted_name:
            found.append(int(info["pid"]))
    return found


class Uia3Driver:
    """Full-tree driver: exact text in, exact text out, no OCR."""

    def __init__(self, min_elements: int = UIA3_MIN_ELEMENTS) -> None:
        self.min_elements = min_elements

    # -- window lookup ----------------------------------------------------
    def find_window(self, profile: dict) -> tuple[Any, dict]:
        """Return ``(wrapper, info)`` for the client's main window."""
        from pywinauto import Desktop

        pids = _pids_for(profile)
        if not pids:
            return None, {"reason": "PROCESS_NOT_FOUND", "pids": []}
        best: tuple[int, int] | None = None
        for pid in pids:
            for handle in _enum_top_level(pid):
                area = _window_area(handle)
                if best is None or area > best[1]:
                    best = (handle, area)
        if best is None:
            return None, {"reason": "NO_VISIBLE_WINDOW", "pids": pids}
        wrapper = Desktop(backend="uia").window(handle=best[0])
        return wrapper, {"handle": best[0], "pids": pids}

    def descendants(self, profile: dict) -> list[Any]:
        wrapper, _info = self.find_window(profile)
        if wrapper is None:
            return []
        return list(wrapper.descendants())

    def has_tree(self, profile: dict) -> bool:
        try:
            return len(self.descendants(profile)) >= self.min_elements
        except Exception:  # noqa: BLE001 - "cannot read it" is the same answer as "no tree"
            return False

    # -- driver protocol --------------------------------------------------
    def observe(self, profile: dict) -> dict:
        wrapper, info = self.find_window(profile)
        if wrapper is None:
            return {"status": "NO_WINDOW", **info}
        try:
            elements = list(wrapper.descendants())
        except Exception as exc:  # noqa: BLE001
            return {"status": "NO_WINDOW", "reason": f"TREE_UNREADABLE: {exc}", **info}
        report: dict[str, Any] = {
            "status": "window-observed",
            "pid": wrapper.element_info.process_id,
            "title": wrapper.window_text(),
            "handle": info.get("handle"),
            "client": "uia3",
            "element_count": len(elements),
            "input_candidate_count": 0,
            "input_found": False,
            "input_ambiguous": False,
            "window_size": self._window_size(wrapper),
        }
        candidates = self.composer_candidates(wrapper)
        report["input_candidate_count"] = len(candidates)
        report["input_found"] = bool(candidates)
        report["input_ambiguous"] = len(candidates) > 1
        if candidates:
            report["input_region"] = self._rect(candidates[0])
        activity, hits = self._activity(elements)
        report["activity_state"] = activity
        report["activity_evidence"] = {"source": "uia3-names", "hits": hits}
        return report

    def capture(self, profile: dict) -> str:
        wrapper, _info = self.find_window(profile)
        if wrapper is None:
            return ""
        parts: list[str] = []
        for element in wrapper.descendants():
            try:
                name = element.element_info.name or ""
            except Exception:  # noqa: BLE001
                name = ""
            if name:
                parts.append(name)
            if (element.element_info.control_type or "") in EDITABLE_TYPES:
                value = self._value(element)
                if value and value != name:
                    parts.append(value)
        return "\n".join(parts)

    # -- strict original-session proof -----------------------------------
    @staticmethod
    def _region_rect(wrapper, region: dict) -> tuple[int, int, int, int] | None:
        """Convert one profile-calibrated normalized region to screen pixels."""
        try:
            outer = wrapper.rectangle()
            left = int(outer.left + outer.width() * float(region["left"]))
            top = int(outer.top + outer.height() * float(region["top"]))
            right = int(outer.left + outer.width() * float(region["right"]))
            bottom = int(outer.top + outer.height() * float(region["bottom"]))
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        if left >= right or top >= bottom:
            return None
        return left, top, right, bottom

    @classmethod
    def _bounded_marker_matches(cls, elements: list[Any], wrapper, *, marker: str,
                                region: dict) -> list[dict]:
        """Find distinct marker leaves inside a calibrated, bounded region.

        The visual tree contains both sidebar rows and top-level container
        names.  Counting arbitrary descendants would allow either one to
        satisfy an "active conversation" proof, so only controls whose own
        centre lies inside the configured region are considered and duplicate
        wrapper/leaf reports at the same geometry are folded together.
        """
        bounds = cls._region_rect(wrapper, region)
        if not marker or bounds is None:
            return []
        left, top, right, bottom = bounds
        found: list[dict] = []
        seen: set[tuple] = set()
        container_types = {"Window", "Pane", "Group", "List", "Tree", "DataGrid"}
        for element in elements:
            try:
                info = element.element_info
                name = str(info.name or "").strip()
                control_type = str(info.control_type or "")
                rect = element.rectangle()
            except Exception:  # noqa: BLE001 - a stale UIA node is not proof
                continue
            if marker not in name or control_type in container_types:
                continue
            centre_x = (int(rect.left) + int(rect.right)) // 2
            centre_y = (int(rect.top) + int(rect.bottom)) // 2
            if not (left <= centre_x < right and top <= centre_y < bottom):
                continue
            key = (name, control_type, centre_x // 8, centre_y // 8)
            if key in seen:
                continue
            seen.add(key)
            found.append({
                "name": name[:300], "control_type": control_type,
                "rect": {"left": int(rect.left), "top": int(rect.top),
                         "right": int(rect.right), "bottom": int(rect.bottom)},
            })
        return found

    @staticmethod
    def _runtime_id(wrapper) -> str:
        """Read a stable UIA runtime identity without guessing from a title.

        ``HWND`` identifies a top-level native window, but Electron can swap
        the renderer/root provider while retaining that handle.  Strict input
        therefore binds both the HWND and the current UIA runtime id.  Different
        pywinauto builds expose it either directly on ``element_info`` or via
        the underlying COM element, hence the deliberately small compatibility
        probe below.  An unreadable id is a refusal, not a reason to weaken the
        surface to HWND-only.
        """
        try:
            info = wrapper.element_info
        except Exception:  # noqa: BLE001
            return ""
        values = [getattr(info, "runtime_id", None)]
        for owner in (getattr(info, "element", None), getattr(info, "_element", None)):
            method = getattr(owner, "GetRuntimeId", None)
            if callable(method):
                try:
                    values.append(method())
                except Exception:  # noqa: BLE001 - no partial identity
                    pass
        for value in values:
            if callable(value):
                try:
                    value = value()
                except Exception:  # noqa: BLE001
                    continue
            if isinstance(value, (list, tuple)):
                parts = [str(item).strip() for item in value if str(item).strip()]
                if parts:
                    return ".".join(parts)
            elif value is not None and str(value).strip():
                return str(value).strip()
        return ""

    @classmethod
    def _surface(cls, profile: dict, wrapper, info: dict) -> tuple[dict | None, str]:
        """Return an attestation-ready native surface, or a refusal reason."""
        try:
            pid = int(wrapper.element_info.process_id)
            title = str(wrapper.window_text() or "").strip()
        except Exception:  # noqa: BLE001
            return None, "ATTESTATION_SURFACE_UNREADABLE"
        actual_exe = process_info.image_path(pid)
        if not actual_exe:
            return None, "ATTESTATION_PROCESS_PATH_UNREADABLE"
        path_match = process_info.executable_match(
            str(profile.get("exe") or ""), actual_exe,
            allowed_roots=profile.get("allowed_exe_roots") or (),
        )
        if path_match.get("match") is not True:
            return None, "ATTESTATION_UNTRUSTED_PROCESS_PATH"
        handle = info.get("handle")
        runtime_id = cls._runtime_id(wrapper)
        if not title or not handle:
            return None, "ATTESTATION_SURFACE_UNREADABLE"
        if not runtime_id:
            return None, "ATTESTATION_RUNTIME_ID_UNREADABLE"
        return {
            "kind": "desktop-uia3",
            "pid": pid,
            "title": title,
            "actual_exe": actual_exe,
            "path_match_mode": str(path_match.get("mode") or ""),
            "path_policy_digest": "",
            "target_id": f"hwnd:{int(handle)}|runtime:{runtime_id}",
        }, ""

    @staticmethod
    def _proof_regions(profile: dict) -> tuple[dict, str]:
        proof = profile.get("session_proof")
        if not isinstance(proof, dict):
            return {}, "ATTESTATION_PROOF_REGIONS_REQUIRED"
        for name in ("session_region", "model_region", "selector_region", "composer_region"):
            region = proof.get(name)
            if not isinstance(region, dict) or set(region) != {"left", "top", "right", "bottom"}:
                return {}, "ATTESTATION_PROOF_REGIONS_REQUIRED"
        return proof, ""

    def _active_session_probe(self, profile: dict, *, allow_select: bool,
                              wrapper=None, info: dict | None = None,
                              expected_surface: dict | None = None) -> dict:
        """Read bounded active/header regions, selecting only when unique."""
        proof, reason = self._proof_regions(profile)
        if reason:
            return {"valid": False, "reason": reason}
        if wrapper is None:
            wrapper, info = self.find_window(profile)
        if info is None:
            info = {}
        if wrapper is None:
            return {"valid": False, "reason": "ATTESTATION_NO_WINDOW", "window": info}
        try:
            elements = list(wrapper.descendants())
        except Exception as exc:  # noqa: BLE001
            return {"valid": False, "reason": "ATTESTATION_TREE_UNREADABLE",
                    "error": str(exc)[:400]}
        if len(elements) < self.min_elements:
            return {"valid": False, "reason": "ATTESTATION_TREE_TOO_SMALL",
                    "element_count": len(elements)}
        session_hits = self._bounded_marker_matches(
            elements, wrapper, marker=str(profile.get("session_marker") or ""),
            region=proof["session_region"])
        model_hits = self._bounded_marker_matches(
            elements, wrapper, marker=str(profile.get("model_marker") or ""),
            region=proof["model_region"])
        capture = {
            "element_count": len(elements), "session_matches": session_hits,
            "model_matches": model_hits, "proof_regions": proof,
        }
        if len(session_hits) == 1 and len(model_hits) == 1:
            surface, surface_reason = self._surface(profile, wrapper, info)
            if surface is None:
                return {"valid": False, "reason": surface_reason, "capture": capture}
            if expected_surface is not None:
                try:
                    same_surface = (session_attestation.surface_digest(surface) ==
                                    session_attestation.surface_digest(expected_surface))
                except (TypeError, ValueError):
                    same_surface = False
                if not same_surface:
                    return {"valid": False, "reason": "ATTESTATION_SURFACE_CHANGED",
                            "capture": capture, "surface": surface}
            return {
                "valid": True, "surface": surface, "capture": capture,
                "evidence": {
                    "transcript_source": "uia3-bounded-tree",
                    "marker_scope": "active-transcript",
                    "session_marker_seen": True, "model_marker_seen": True,
                    "session_switch": {"attempted": False, "unique": True,
                                       "result": "already-active"},
                    "focus_changed": False, "input_attempted": False,
                    "input_submitted": False,
                },
            }
        if not allow_select:
            return {"valid": False,
                    "reason": ("ATTESTATION_ACTIVE_SESSION_AMBIGUOUS" if len(session_hits) > 1
                               else "ATTESTATION_ACTIVE_SESSION_NOT_VISIBLE"),
                    "capture": capture}
        selector_hits = self._bounded_marker_matches(
            elements, wrapper, marker=str(profile.get("session_marker") or ""),
            region=proof["selector_region"])
        capture["selector_matches"] = selector_hits
        if len(selector_hits) != 1:
            return {"valid": False,
                    "reason": ("ATTESTATION_SELECTOR_AMBIGUOUS" if selector_hits
                               else "ATTESTATION_SELECTOR_NOT_FOUND"),
                    "capture": capture}
        # Re-find the exact UIA element from its bounded, unique geometry.  A
        # stale object after a renderer reflow is never clicked as a fallback.
        desired = selector_hits[0]
        selected_element = None
        for element in elements:
            try:
                rect = element.rectangle()
                name = str(element.element_info.name or "")
            except Exception:  # noqa: BLE001
                continue
            if name == desired["name"] and int(rect.left) == desired["rect"]["left"] and \
                    int(rect.top) == desired["rect"]["top"]:
                selected_element = element
                break
        if selected_element is None:
            return {"valid": False, "reason": "ATTESTATION_SELECTOR_STALE",
                    "capture": capture}
        try:
            selected_element.click_input()
        except Exception as exc:  # noqa: BLE001
            return {"valid": False, "reason": "ATTESTATION_SELECTOR_CLICK_FAILED",
                    "error": str(exc)[:400], "capture": capture}
        time.sleep(0.6)
        after = self._active_session_probe(profile, allow_select=False)
        if after.get("valid") is not True:
            after.setdefault("capture", capture)
            return after
        after["evidence"] = dict(after["evidence"])
        after["evidence"]["session_switch"] = {
            "attempted": True, "unique": True, "result": "selected"}
        after["evidence"]["focus_changed"] = True
        return after

    def attest_original_session(self, profile: dict) -> dict:
        """Prove the configured conversation in bounded active UIA regions.

        This is intentionally separate from :meth:`capture`: the latter is a
        convenient full-window transcript reader, while strict pre-input proof
        must reject sidebar/search/whole-window marker collisions.
        """
        return self._active_session_probe(profile, allow_select=True)

    def select_session(self, profile: dict) -> dict:
        """Click the first element whose name carries the session marker."""
        marker = str(profile.get("session_marker") or "")
        if not marker:
            return {"status": "SESSION_MARKER_MISSING"}
        wrapper, _info = self.find_window(profile)
        if wrapper is None:
            return {"status": "NO_WINDOW"}
        for element in wrapper.descendants():
            try:
                name = element.element_info.name or ""
            except Exception:  # noqa: BLE001
                continue
            if marker not in name:
                continue
            try:
                element.click_input()
            except Exception as exc:  # noqa: BLE001
                return {"status": "session-click-failed", "error": str(exc)[:400], "name": name[:200]}
            time.sleep(0.6)
            return {"status": "session-selection-attempted", "name": name[:200], "client": "uia3"}
        return {"status": "session-not-found", "session_marker": marker}

    def launch(self, profile: dict) -> None:
        executable = Path(profile["exe"])
        if not executable.is_file():
            raise RuntimeError("EXECUTABLE_MISSING")
        subprocess.Popen([str(executable)], cwd=str(executable.parent), close_fds=True)

    def deliver(self, profile: dict, message: str, event_id: str, *, urgent: bool = False,
                composer_ratios: tuple[float, float] | None = None,
                expected_surface: dict | None = None) -> dict:
        """Type through the tree, verify by reading the tree, then press Enter."""
        wrapper, info = self.find_window(profile)
        if wrapper is None:
            return {"status": "NO_WINDOW", "task_delivered": False, **info}
        if expected_surface is not None:
            current_surface, surface_reason = self._surface(profile, wrapper, info)
            if current_surface is None:
                return {"status": "attestation-surface-changed", "task_delivered": False,
                        "reason": surface_reason}
            try:
                same_surface = (session_attestation.surface_digest(current_surface) ==
                                session_attestation.surface_digest(expected_surface))
            except (TypeError, ValueError):
                same_surface = False
            if not same_surface:
                return {"status": "attestation-surface-changed", "task_delivered": False,
                        "surface": current_surface}
        strict_proof: dict | None = None
        if expected_surface is not None:
            strict_proof, proof_reason = self._proof_regions(profile)
            if proof_reason:
                return {"status": "attestation-surface-changed", "task_delivered": False,
                        "reason": proof_reason}
        candidates = self.composer_candidates(
            wrapper, region=(strict_proof or {}).get("composer_region"))
        if not candidates:
            if expected_surface is not None:
                return {"status": "attestation-surface-changed", "task_delivered": False,
                        "reason": "ATTESTATION_COMPOSER_NOT_IN_REGION",
                        "element_count": len(list(wrapper.descendants())), "client": "uia3"}
            return {"status": "NO_INPUT_CONTROL", "task_delivered": False,
                    "element_count": len(list(wrapper.descendants())), "client": "uia3"}
        composer = candidates[0]
        if expected_surface is not None:
            # The same HWND can retain its identity while its active chat pane
            # changes.  Focus the exact bounded composer, then re-read bounded
            # active/session/model evidence from this same wrapper immediately
            # before the first write.  A later fallback must never type based
            # on the earlier proof alone.
            try:
                composer.set_focus()
            except Exception:  # noqa: BLE001
                return {"status": "attestation-surface-changed", "task_delivered": False,
                        "reason": "ATTESTATION_COMPOSER_FOCUS_FAILED"}
            recheck = self._active_session_probe(
                profile, allow_select=False, wrapper=wrapper, info=info,
                expected_surface=expected_surface)
            if recheck.get("valid") is not True:
                return {"status": "attestation-surface-changed", "task_delivered": False,
                        "reason": str(recheck.get("reason") or
                                      "ATTESTATION_ACTIVE_SESSION_NOT_VISIBLE"),
                        "capture": recheck.get("capture", {})}
        write_method, typed = self._write(composer, message, event_id,
                                          focus=expected_surface is None)
        if not typed:
            return {"status": "input-not-confirmed", "task_delivered": False,
                    "write_method": write_method, "client": "uia3",
                    "typed_preview": self._value(composer)[:200]}
        send_method = self._send(composer, profile, wrapper=wrapper)
        if not send_method:
            return {"status": "send-control-not-found", "task_delivered": False,
                    "write_method": write_method, "client": "uia3"}
        # A read-back window, not a reply window: this only has to prove the
        # message left the composer and landed in the conversation.
        deadline = time.monotonic() + float(profile.get("readback_timeout_seconds", 10) or 10)
        visible = False
        polls = 0
        text = ""
        while time.monotonic() < deadline:
            polls += 1
            time.sleep(1.0)
            text = self.capture(profile)
            if event_id in text and event_id not in self._value(composer):
                visible = True
                break
        return {
            "status": "message-visible" if visible else "submitted-unconfirmed",
            "task_delivered": visible,
            "write_method": write_method,
            "send_method": send_method,
            "readback_polls": polls,
            "event_id_visible": event_id in text,
            "client": "uia3",
        }

    # -- internals --------------------------------------------------------
    @staticmethod
    def _window_size(wrapper) -> dict | None:
        try:
            rect = wrapper.rectangle()
        except Exception:  # noqa: BLE001
            return None
        return {"width": int(rect.width()), "height": int(rect.height())}

    @staticmethod
    def _rect(element) -> dict | None:
        try:
            rect = element.rectangle()
        except Exception:  # noqa: BLE001
            return None
        return {"x": int(rect.left), "y": int(rect.top),
                "width": int(rect.width()), "height": int(rect.height())}

    @staticmethod
    def _value(element) -> str:
        try:
            return str(element.iface_value.CurrentValue or "")
        except Exception:  # noqa: BLE001 - plenty of controls have no ValuePattern
            return ""

    def composer_candidates(self, wrapper, *, region: dict | None = None) -> list[Any]:
        """Editable, enabled, focusable controls ordered by screen position.

        The lowest one wins because a chat composer sits under the transcript
        and a search field sits above it; the previous adapter encoded that as
        a sort key after learning it the hard way on a client whose URL bar was
        the first editable control in the tree.
        """
        found: list[Any] = []
        for element in wrapper.descendants():
            info = element.element_info
            if (info.control_type or "") not in EDITABLE_TYPES:
                continue
            try:
                if not info.enabled:
                    continue
                if not element.is_keyboard_focusable():
                    continue
            except Exception:  # noqa: BLE001
                continue
            if region is not None:
                bounds = self._region_rect(wrapper, region)
                try:
                    rect = element.rectangle()
                except Exception:  # noqa: BLE001
                    continue
                if bounds is None or not (
                    bounds[0] <= int(rect.left) and int(rect.right) <= bounds[2] and
                    bounds[1] <= int(rect.top) and int(rect.bottom) <= bounds[3]
                ):
                    continue
            found.append(element)

        def _bottom(element) -> int:
            try:
                return int(element.rectangle().bottom)
            except Exception:  # noqa: BLE001
                return 0

        found.sort(key=_bottom)
        return found

    def _activity(self, elements: list[Any]) -> tuple[str, list[str]]:
        names = []
        for element in elements:
            try:
                name = (element.element_info.name or "").strip()
            except Exception:  # noqa: BLE001
                continue
            if name:
                names.append(name.casefold())
        hits = [pattern for pattern in BUSY_PATTERNS if any(pattern in name for name in names)]
        hits += sorted(name for name in names if name in BUSY_EXACT)
        if hits:
            return "busy", hits
        ready = [pattern for pattern in READY_PATTERNS if any(pattern in name for name in names)]
        if ready:
            return "ready", ready
        return "unknown", []

    def _write(self, composer, message: str, event_id: str, *, focus: bool = True) -> tuple[str, bool]:
        """Put the text in the composer and prove it by reading it back.

        The proof is the event id, not "the field is non-empty": a composer that
        already held a human draft is non-empty before this runs, so a weaker
        check would report success for a write that never happened.
        """
        if focus:
            try:
                composer.set_focus()
            except Exception:  # noqa: BLE001 - focus is helpful, not required
                pass
        try:
            composer.iface_value.SetValue(message)
            if event_id in self._value(composer):
                return "uia3-valuepattern", True
        except Exception:  # noqa: BLE001
            pass
        # Chromium's contenteditable often exposes ValuePattern as read-only.
        # Typing CJK key by key is not reliable, so the clipboard is the
        # fallback -- the same mechanism the OCR adapter uses.
        try:
            self._set_clipboard(message)
            composer.type_keys("^a^v")
            time.sleep(0.4)
            return "uia3-clipboard-paste", event_id in self._value(composer)
        except Exception:  # noqa: BLE001
            return "uia3-write-failed", False

    def _send(self, composer, profile: dict, *, wrapper=None) -> str:
        mode = str(profile.get("send_mode") or "enter")
        if mode == "button":
            if wrapper is not None:
                for element in wrapper.descendants():
                    try:
                        name = (element.element_info.name or "").strip()
                        ctype = element.element_info.control_type or ""
                    except Exception:  # noqa: BLE001
                        continue
                    if ctype == "Button" and name.casefold() in {"send", "发送", "submit"}:
                        try:
                            element.click_input()
                            return "uia3-send-button"
                        except Exception:  # noqa: BLE001
                            break
        try:
            composer.type_keys("{ENTER}")
            return "uia3-enter"
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _set_clipboard(text: str) -> None:
        import win32clipboard
        import win32con

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()


class AutoDriver:
    """Use the full-tree client where it can see a tree, the OCR adapter otherwise.

    The choice is made per call from the tree that is actually present.  A
    profile flag would be cheaper and would also be a promise the application
    can break without telling anyone -- which is exactly what happened here:
    the same client exposed 1090 elements on one launch and 9 on the next,
    because renderer accessibility is decided at startup.
    """

    def __init__(self, fallback=None, uia3: Uia3Driver | None = None) -> None:
        self.uia3 = uia3 or Uia3Driver()
        self._fallback = fallback

    @property
    def fallback(self):
        if self._fallback is None:
            from agent_evolution_protocol.coordination.ui_fallback import PowerShellUiaDriver
            self._fallback = PowerShellUiaDriver()
        return self._fallback

    def _use_uia3(self, profile: dict) -> bool:
        # Opt-in per profile.  A tree that exists is not the same thing as a
        # tree that submits: this driver presses Enter on the composer element,
        # and a client whose submit path needs a real button click would then
        # report ``submitted-unconfirmed`` where the OCR adapter reported
        # ``message-visible``.  Enabling it silently for every client would
        # trade a measured working path for an unmeasured one.
        if not profile.get("prefer_uia3", False):
            return False
        try:
            return self.uia3.has_tree(profile)
        except Exception:  # noqa: BLE001
            return False

    def observe(self, profile: dict) -> dict:
        if not self._use_uia3(profile):
            return self.fallback.observe(profile)
        report = self.uia3.observe(profile)
        if profile.get("activity_ocr"):
            # Structure comes from the tree; "is it generating right now" comes
            # from the pixels, because the busy indicator is an animation with
            # no accessible name.  Losing that check would let a dispatch type
            # over a live run.
            try:
                pixel = self.fallback.observe(profile)
                report["activity_state"] = pixel.get("activity_state", report.get("activity_state"))
                report["activity_evidence"] = pixel.get("activity_evidence", report.get("activity_evidence"))
            except Exception:  # noqa: BLE001
                pass
        return report

    def capture(self, profile: dict) -> str:
        if not self._use_uia3(profile):
            return self.fallback.capture(profile)
        text = self.uia3.capture(profile)
        return text or self.fallback.capture(profile)

    def select_session(self, profile: dict) -> dict:
        if not self._use_uia3(profile):
            return self.fallback.select_session(profile)
        result = self.uia3.select_session(profile)
        if result.get("status") == "session-not-found":
            return self.fallback.select_session(profile)
        return result

    def attest_original_session(self, profile: dict) -> dict:
        # Strict proof deliberately does not fall back to OCR.  A whole-window
        # OCR capture cannot distinguish a target title in the sidebar from an
        # active conversation header, so it is an observation aid, not input
        # authorization.
        try:
            if self.uia3.has_tree(profile):
                return self.uia3.attest_original_session(profile)
        except Exception as exc:  # noqa: BLE001
            return {"valid": False, "reason": "ATTESTATION_PROBE_FAILED",
                    "error": str(exc)[:400]}
        return {"valid": False, "reason": "ATTESTATION_UNSUPPORTED_BY_DRIVER",
                "client": "uia3-tree-unavailable"}

    def launch(self, profile: dict) -> None:
        return self.fallback.launch(profile)

    def deliver(self, profile: dict, message: str, event_id: str, *, urgent: bool = False,
                composer_ratios: tuple[float, float] | None = None,
                expected_surface: dict | None = None) -> dict:
        if expected_surface is not None and (urgent or not self._use_uia3(profile)):
            return {"status": "attestation-surface-unsupported", "task_delivered": False}
        if not self._use_uia3(profile) or urgent:
            # The non-interrupting interject control is a pixel/OCR construct;
            # the tree cannot name it, so urgent stays on the adapter that can.
            return self.fallback.deliver(profile, message, event_id, urgent=urgent,
                                         composer_ratios=composer_ratios)
        result = self.uia3.deliver(profile, message, event_id, composer_ratios=composer_ratios,
                                   expected_surface=expected_surface)
        if result.get("status") in {"NO_WINDOW", "NO_INPUT_CONTROL", "uia3-write-failed"}:
            if expected_surface is not None:
                # The bounded UIA proof belongs to this native surface only.
                # OCR cannot bind a write to it, so a strict route must hold
                # rather than silently send through the fallback adapter.
                return {"status": "attestation-surface-changed", "task_delivered": False,
                        "reason": "ATTESTATION_UIA3_DELIVERY_UNAVAILABLE",
                        "uia3_report": result}
            return self.fallback.deliver(profile, message, event_id, urgent=urgent,
                                         composer_ratios=composer_ratios)
        return result


def driver_for(profile: dict):
    """The driver a caller should use for this profile.

    Kept as one function so the scheduled path, the readiness probe and the
    operator command cannot drift into three different policies.
    """
    if not available():
        from agent_evolution_protocol.coordination.ui_fallback import PowerShellUiaDriver
        return PowerShellUiaDriver()
    return AutoDriver()
