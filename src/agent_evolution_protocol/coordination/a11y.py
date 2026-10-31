"""Turn Windows accessibility on so Electron clients expose a real element tree.

Chromium -- and therefore every Electron client (Qoder, ChatGPT, Claude,
WorkBuddy) -- builds its accessibility tree only when the OS reports that a
screen reader is present.  Without it a whole window reports three elements and
no editable control, which is why this project fell back to OCR and to clicking
at a guessed ratio of the window.

Measured on this host, same window, same moment:

    ChatGPT   3 elements, no input, text only via OCR
              -> 400 elements, 3 input candidates, 60978 characters from UIA

The switch is ``SPI_SETSCREENREADER``.  Chromium watches ``WM_SETTINGCHANGE``,
so a window that is already running picks it up; nothing needs restarting.  By
default the change is runtime-only (no ``SPIF_UPDATEINIFILE``), so a reboot
undoes it.

One limit is not solvable here: Windows UIPI stops a medium-integrity process
from driving a higher-integrity window.  If the target client runs elevated,
the executor must run elevated too, and ``elevation()`` reports that instead of
letting a later step fail mysteriously.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

SPI_GETSCREENREADER = 0x0046
SPI_SETSCREENREADER = 0x0047
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenElevation = 20


def available() -> bool:
    return sys.platform == "win32"


def _user32():
    if not available():
        raise RuntimeError("accessibility control is Windows-only")
    return ctypes.WinDLL("user32", use_last_error=True)


def status() -> bool:
    """Is the system currently reporting a screen reader?"""
    user32 = _user32()
    value = wintypes.BOOL()
    ok = user32.SystemParametersInfoW(SPI_GETSCREENREADER, 0,
                                      ctypes.byref(value), 0)
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(value.value)


def set_enabled(enabled: bool, *, persist: bool = False) -> bool:
    """Report or stop reporting a screen reader; returns the resulting state.

    ``persist`` also writes the user profile, so the setting survives a reboot.
    The default is deliberately the reversible one.
    """
    user32 = _user32()
    flags = SPIF_SENDCHANGE | (SPIF_UPDATEINIFILE if persist else 0)
    ok = user32.SystemParametersInfoW(SPI_SETSCREENREADER, 1 if enabled else 0,
                                      None, flags)
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return status()


def ensure() -> bool:
    """Turn it on only if it is off; returns whether anything changed."""
    if status():
        return False
    set_enabled(True)
    return True


class _TokenElevation(ctypes.Structure):
    _fields_ = [("TokenIsElevated", wintypes.DWORD)]


def _api():
    """Win32 handles with explicit signatures.

    Without argtypes ctypes treats a HANDLE as a C int: the pseudo-handle from
    GetCurrentProcess() is -1 and overflows, and a real handle can be truncated
    on 64-bit.  Both failures look like "access denied", which is exactly the
    answer this module reports, so they must not be possible.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                             ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    return kernel32, advapi32


def _elevation_from(token, kernel32) -> str:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    try:
        value = _TokenElevation()
        size = wintypes.DWORD()
        ok = advapi32.GetTokenInformation(token, TokenElevation,
                                          ctypes.byref(value),
                                          ctypes.sizeof(value),
                                          ctypes.byref(size))
        if not ok:
            return "unknown"
        return "elevated" if value.TokenIsElevated else "standard"
    finally:
        kernel32.CloseHandle(token)


def elevation(pid: int) -> str:
    """Report ``standard``/``elevated`` for a process, or ``unknown``.

    ``unknown`` is the interesting answer: Windows refuses to hand over the
    token of a process running at a higher integrity level, which also means
    UIA, ``ShowWindow`` and injected input will be refused later.  Saying so
    here is better than a silent no-op three steps downstream.
    """
    if not available():
        return "unknown"
    kernel32, advapi32 = _api()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return "unknown"
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return "unknown"
        return _elevation_from(token, kernel32)
    finally:
        kernel32.CloseHandle(handle)


def check(pid: int | None = None) -> dict:
    """Everything a caller needs before trying to drive a window."""
    report = {"available": available(), "screen_reader": None, "changed": False}
    if not report["available"]:
        return report
    report["changed"] = ensure()
    report["screen_reader"] = status()
    if pid is not None:
        report["target_elevation"] = elevation(pid)
        report["executor_elevation"] = _self_elevation()
        # ``unknown`` means UIPI will refuse window control later; saying it
        # here is cheaper than discovering it when nothing gets typed.
        report["drivable"] = report["target_elevation"] != "unknown"
    return report


def _self_elevation() -> str:
    if not available():
        return "unknown"
    kernel32, advapi32 = _api()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY,
                                     ctypes.byref(token)):
        return "unknown"
    return _elevation_from(token, kernel32)
