"""Read-only process start-time identity for heartbeat liveness checks.

A PID alone is not an identity: the OS may reuse it after the original host
process exits.  Heartbeat writers include ``process_started_at``; readers can
compare it with the current start time of the same PID.  On platforms where
the check is unavailable the answer is ``None``, never ``True``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os

from ..isotime import parse_iso_or_none

_FIRST_FILETIME = datetime(1601, 1, 1, tzinfo=timezone.utc)
_TOLERANCE = timedelta(seconds=2)


def _filetime_to_datetime(value: int) -> datetime:
    return _FIRST_FILETIME + timedelta(microseconds=value / 10)


def _started_at_windows(pid: int) -> datetime | None:
    try:
        import ctypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", ctypes.c_uint32),
                        ("dwHighDateTime", ctypes.c_uint32)]

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            kernel32.GetProcessTimes.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
                ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
            ]
            kernel32.GetProcessTimes.restype = ctypes.c_bool
            if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                            ctypes.byref(kernel), ctypes.byref(user)):
                return None
        finally:
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle(handle)
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return _filetime_to_datetime(ticks) if ticks else None
    except (AttributeError, OSError, ValueError, OverflowError):
        return None


def _started_at_posix(pid: int) -> datetime | None:
    """Best-effort Linux/macOS fallback; returns None when unavailable."""
    try:
        stat = os.stat(f"/proc/{pid}")
        return datetime.fromtimestamp(stat.st_ctime, timezone.utc)
    except (OSError, ValueError, OverflowError):
        return None


def started_at(pid: int | None = None) -> datetime | None:
    """UTC process creation time, or None when it cannot be established."""
    target = os.getpid() if pid is None else pid
    if not isinstance(target, int) or isinstance(target, bool) or target <= 0:
        return None
    if os.name == "nt":
        return _started_at_windows(target)
    return _started_at_posix(target)


def current_started_at() -> str:
    """ISO-8601 creation time to embed in a heartbeat; empty when unavailable."""
    value = started_at()
    return value.isoformat(timespec="microseconds") if value else ""


def verify(pid: int, claimed_started_at: str) -> bool | None:
    """True if the PID still belongs to the process that wrote the heartbeat."""
    claim = parse_iso_or_none(claimed_started_at, assume_utc=True)
    actual = started_at(pid)
    if claim is None or actual is None:
        return None
    if claim.tzinfo is None:
        claim = claim.replace(tzinfo=timezone.utc)
    return abs(claim.astimezone(timezone.utc) - actual) <= _TOLERANCE
