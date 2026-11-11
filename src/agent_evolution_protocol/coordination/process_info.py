"""Identify a process when its executable is the thing in question.

Two APIs look like they answer this and do not:

* ``Process.MainModule`` throws for a process at a higher integrity level;
* ``Get-Process | Select-Object Path`` returns an empty string for it.

That is precisely the case this project creates on purpose -- the clients run
elevated so an agent does not stall on UAC -- so a path check written that way
is silently skipped for exactly the processes that need it.  The driver then
binds a window to a profile entry nobody verified.

``QueryFullProcessImageNameW`` needs only ``PROCESS_QUERY_LIMITED_INFORMATION``,
the same access the elevation check already obtains, so it answers for an
elevated target as well.
"""
from __future__ import annotations

import ctypes
import ntpath
import os
import sys
from ctypes import wintypes

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def available() -> bool:
    return sys.platform == "win32"


def _api():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                    wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    return kernel32


def image_path(pid: int) -> str:
    """Full path of the executable behind ``pid``, or ``""`` when unreadable."""
    if not available():
        return ""
    kernel32 = _api()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def _normalise(value: str) -> str:
    return os.path.normcase(os.path.normpath(value)) if value else ""


def _uses_windows_path(value: str) -> bool:
    """Whether a value needs Windows path semantics even under Linux CI."""
    return (len(value) >= 3 and value[1:3] in (":\\", ":/")) or value.startswith(("\\\\", "//"))


def _path_module(*values: str):
    """Pick a path implementation without making Windows-only tests flaky."""
    return ntpath if any(_uses_windows_path(str(value or "")) for value in values) else os.path


def normalise_allowed_exe_roots(roots) -> list[str]:
    """Validate the narrow version-directory policy used for executable trust.

    A permitted updater root is intentionally broader than a pinned executable,
    so it must never be a whole volume or UNC share.  Keeping this validation
    next to :func:`executable_match` ensures discovery, UI profiles,
    fingerprints and future attestation gates cannot each invent a looser
    spelling of the policy.
    """
    if roots is None:
        return []
    if not isinstance(roots, (list, tuple)):
        raise ValueError("allowed_exe_roots must be a list of absolute directories")
    clean: list[str] = []
    seen: set[str] = set()
    for item in roots:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("allowed_exe_roots must contain non-empty absolute directories")
        value = item.strip()
        paths = _path_module(value)
        if not paths.isabs(value):
            raise ValueError("allowed_exe_roots must contain absolute directories")
        normalized = paths.normcase(paths.normpath(value))
        drive, tail = paths.splitdrive(normalized)
        # ``dirname`` catches POSIX and drive roots.  The explicit splitdrive
        # check covers a UNC share root, where the share itself is the drive.
        if (paths.dirname(normalized) == normalized or
                (drive and tail in ("\\", "/"))):
            raise ValueError("allowed_exe_roots must not be a filesystem root")
        if normalized in seen:
            raise ValueError("allowed_exe_roots must not contain duplicate directories")
        seen.add(normalized)
        clean.append(value)
    return clean


def executable_match(expected: str, actual: str, *, allowed_roots=()) -> dict:
    """Compare a process image to a profile, permitting explicit version roots.

    A desktop client often replaces ``...\\versions\\0.5.3\\Client.exe`` with
    a newer version directory.  Treating that as an automatic path match is
    unsafe: two products can have overlapping display names (for example
    Qoder and Qoder CN).  A root is therefore opt-in, boundary-aware, and is
    accepted only when the executable file name is still exactly the one the
    profile named.
    """
    result = {"match": False, "mode": "mismatch", "allowed_root": "", "reason": ""}
    if not expected or not actual:
        result["reason"] = "expected and actual executable paths are required"
        return result
    try:
        roots = tuple(normalise_allowed_exe_roots(allowed_roots))
    except ValueError as exc:
        result["reason"] = f"invalid allowed executable roots: {exc}"
        return result
    paths = _path_module(expected, actual, *roots)
    expected_normal = paths.normcase(paths.normpath(expected))
    actual_normal = paths.normcase(paths.normpath(actual))
    if actual_normal == expected_normal:
        return {"match": True, "mode": "exact", "allowed_root": "", "reason": ""}
    if paths.normcase(paths.basename(actual)) != paths.normcase(paths.basename(expected)):
        result["reason"] = "the running executable name differs from the profile executable"
        return result
    for root in roots:
        if not isinstance(root, str) or not root.strip():
            continue
        root_normal = paths.normcase(paths.normpath(root.strip()))
        try:
            within_root = paths.commonpath([actual_normal, root_normal]) == root_normal
        except ValueError:
            # Different drives or incompatible path flavours cannot form a
            # containment relationship.
            within_root = False
        if within_root:
            return {"match": True, "mode": "trusted-root", "allowed_root": root,
                    "reason": ""}
    result["reason"] = "the running executable is not the path this profile names or an allowed version root"
    return result


def path_check(expected: str, pid, *, allowed_roots=()) -> dict:
    """Does the running process match the path a profile claims?

    ``match: None`` means "not checked", and ``reason`` says why.  Exact path
    identity is the default; an explicit version root may deliberately admit
    a same-named binary beneath it and is recorded as ``trusted-root``.
    """
    result = {"expected": expected or "", "actual": "", "match": None,
              "match_mode": "", "allowed_roots": list(allowed_roots or ()),
              "allowed_root": "", "reason": ""}
    if not expected:
        result["reason"] = "no expected path configured"
        return result
    if not pid:
        result["reason"] = "no pid to compare"
        return result
    if not available():
        result["reason"] = "process image paths are Windows-only"
        return result
    actual = image_path(int(pid))
    result["actual"] = actual
    if not actual:
        result["reason"] = "the image path could not be read"
        return result
    verdict = executable_match(expected, actual, allowed_roots=allowed_roots)
    result["match"] = verdict["match"]
    result["match_mode"] = verdict["mode"]
    result["allowed_root"] = verdict["allowed_root"]
    result["reason"] = verdict["reason"]
    return result
