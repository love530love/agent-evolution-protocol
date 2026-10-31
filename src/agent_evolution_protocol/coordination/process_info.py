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


def path_check(expected: str, pid) -> dict:
    """Does the running process match the path a profile claims?

    ``match: None`` means "not checked", and ``reason`` says why.  A profile
    that points at an old version directory keeps working only because
    ``allow_unknown_path`` lets it through, so the drift has to be visible
    somewhere instead of being inferred from a missing warning.
    """
    result = {"expected": expected or "", "actual": "", "match": None, "reason": ""}
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
    result["match"] = _normalise(actual) == _normalise(expected)
    if not result["match"]:
        result["reason"] = "the running executable is not the path this profile names"
    return result
