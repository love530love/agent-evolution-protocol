"""Read-only process start-time identity for heartbeat liveness checks.

A PID alone is not an identity: the OS may reuse it after the original host
process exits.  Heartbeat writers include ``process_started_at``; readers can
compare it with the current start time of the same PID.  On platforms where
the check is unavailable the answer is ``None``, never ``True``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import re

from ..isotime import parse_iso_or_none

_FIRST_FILETIME = datetime(1601, 1, 1, tzinfo=timezone.utc)
_TOLERANCE = timedelta(seconds=2)


def alive(pid: int) -> bool | None:
    """Read-only process existence probe; access errors are not death proof."""
    if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid <= 0xFFFFFFFF:
        return None
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            if os.path.isdir("/proc"):
                try:
                    with open(f"/proc/{pid}/stat", encoding="ascii") as stat_file:
                        state = stat_file.read().rsplit(")", 1)[1].strip().split()[0]
                    if state in {"Z", "X"}:
                        return False
                except (OSError, IndexError, ValueError):
                    return None
            return True
        except ProcessLookupError:
            return False
        except (PermissionError, OSError, OverflowError):
            return None
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            # ERROR_INVALID_PARAMETER identifies a nonexistent PID; notably,
            # ACCESS_DENIED and unknown OS errors cannot prove it has exited.
            return False if ctypes.get_last_error() == 87 else None
        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            kernel32.GetExitCodeProcess.restype = ctypes.c_bool
            return code.value == 259 if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) else None
        finally:
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError, OverflowError):
        return None


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


def verify_precise(pid: int, claimed_started_at: str) -> bool | None:
    """Compare a timezone-aware creation token with at least ms precision.

    Legacy coarse timestamps and unavailable OS creation times are unknown;
    the wider two-second legacy tolerance could accept a quickly reused PID.
    """
    if not isinstance(claimed_started_at, str) or not re.search(
            r"T\d{2}:\d{2}:\d{2}\.\d{3,}(?:Z|[+-]\d{2}:?\d{2})$", claimed_started_at):
        return None
    claim = parse_iso_or_none(claimed_started_at)
    actual = started_at(pid)
    if claim is None or claim.tzinfo is None or actual is None:
        return None
    return abs(claim.astimezone(timezone.utc) - actual) <= timedelta(milliseconds=1)
