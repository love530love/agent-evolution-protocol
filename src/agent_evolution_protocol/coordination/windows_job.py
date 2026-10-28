"""Small Windows Job Object owner for trusted validation subprocesses.

Only the parent creates a job. A worker opens that existing job, assigns itself,
and closes its own handle before running tests. The parent's noninheritable
handle then controls the lifetime of all normally created job descendants.
This is process containment, not a security sandbox or a rollback mechanism.
"""

import ctypes
from ctypes import wintypes
import hashlib
import os
import re


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000
JOB_OBJECT_ASSIGN_PROCESS = 0x0001
JOB_OBJECT_QUERY = 0x0004
JOB_OBJECT_TERMINATE = 0x0008
ERROR_FILE_NOT_FOUND = 2
ERROR_ALREADY_EXISTS = 183


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimit), ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


def _api():
    if os.name != "nt":
        raise OSError("Windows Job Objects are unavailable on this platform")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    # Explicit pointer-width handles are required on 64-bit Windows.
    declarations = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "OpenJobObjectW": ([wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "ProcessIdToSessionId": ([wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
    }
    for name, (arguments, result) in declarations.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


def _name(name):
    if not isinstance(name, str) or re.fullmatch(r"AEPValidation-[0-9a-f]{32}", name) is None:
        raise ValueError("invalid validation job name")
    return name


def execution_context():
    """Bind session-local object names to this Windows installation/session.

    Fail closed if the installation identifier cannot be read. A hostname alone
    would not safely distinguish a copied run directory on another machine.
    """
    api = _api()
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                       0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        machine, _ = winreg.QueryValueEx(key, "MachineGuid")
    if not isinstance(machine, str) or not machine.strip():
        raise ValueError("Windows machine identifier unavailable")
    session_id = wintypes.DWORD()
    if not api.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"machine": hashlib.sha256(machine.strip().lower().encode("utf-8")).hexdigest(),
            "session_id": session_id.value}


class Job:
    def __init__(self, name, handle, api):
        self.name, self._handle, self._api = name, handle, api

    @classmethod
    def create(cls, name):
        name, api = _name(name), _api()
        ctypes.set_last_error(0)
        handle = api.CreateJobObjectW(None, name)
        error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(error)
        # Never configure or terminate a pre-existing object, even on collision.
        if error == ERROR_ALREADY_EXISTS:
            api.CloseHandle(handle)
            raise FileExistsError("validation job already exists")
        job = cls(name, handle, api)
        try:
            limits = _ExtendedLimit()
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not api.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if job.limit_flags() != JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE:
                raise OSError("validation job containment limits were not applied")
        except BaseException:
            job.close()
            raise
        return job

    @classmethod
    def open(cls, name):
        """Open only; absence is distinct from denied access or other failures."""
        name, api = _name(name), _api()
        handle = api.OpenJobObjectW(JOB_OBJECT_QUERY | JOB_OBJECT_ASSIGN_PROCESS | JOB_OBJECT_TERMINATE,
                                    False, name)
        if not handle:
            error = ctypes.get_last_error()
            if error == ERROR_FILE_NOT_FOUND:
                return None
            raise ctypes.WinError(error)
        return cls(name, handle, api)

    def _require_handle(self):
        if self._handle is None:
            raise ValueError("validation job handle is closed")
        return self._handle

    def limit_flags(self):
        limits = _ExtendedLimit()
        if not self._api.QueryInformationJobObject(self._require_handle(), 9, ctypes.byref(limits),
                                                   ctypes.sizeof(limits), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return limits.BasicLimitInformation.LimitFlags

    def assign_current_process(self):
        flags = self.limit_flags()
        if (not flags & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                or flags & (JOB_OBJECT_LIMIT_BREAKAWAY_OK | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK)):
            raise ValueError("validation job does not enforce required containment")
        if not self._api.AssignProcessToJobObject(self._require_handle(), self._api.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self):
        if not self._api.TerminateJobObject(self._require_handle(), 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def active_processes(self):
        accounting = _Accounting()
        if not self._api.QueryInformationJobObject(self._require_handle(), 1, ctypes.byref(accounting),
                                                   ctypes.sizeof(accounting), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return accounting.ActiveProcesses

    def close(self):
        if self._handle is not None:
            if not self._api.CloseHandle(self._handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = None

    def __enter__(self):
        self._require_handle()
        return self

    def __exit__(self, *_):
        self.close()
