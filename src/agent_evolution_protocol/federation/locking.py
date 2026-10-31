"""Cross-process exclusive lock so two schedulers cannot lose each other's work.

The federation kernel does read-modify-write on ``state.json``. Without a lock
two schedulers both read the same snapshot, both append, and the second write
silently erases the first. The event log would still show both attempts, which
makes the damage hard to notice: the state and the audit trail disagree.

The lock is advisory and file based, so it works for any process that agrees to
take it, including a different language. It is re-entrant **within one process**
so ``save()`` can be called while a caller already holds the workspace lock;
across processes it is strictly exclusive.

POSIX uses ``flock``; Windows uses ``msvcrt.locking`` on the first byte. Both are
released by the kernel if the process dies, so a crash cannot leave the
workspace permanently locked.
"""
from __future__ import annotations

import os
from pathlib import Path
import threading
import time

if os.name == "nt":  # pragma: no cover - platform switch
    import msvcrt
else:  # pragma: no cover - platform switch
    import fcntl


DEFAULT_TIMEOUT = 10.0
POLL_SECONDS = 0.02

#: path -> [depth, fd]. Depth keeps ``save()`` from deadlocking when the caller
#: already holds the same workspace lock.
_HELD: dict[str, list] = {}
_REGISTRY = threading.Lock()


class LockTimeout(RuntimeError):
    """Raised instead of blocking forever when the lock is not available."""


def _try_lock(handle) -> bool:
    try:
        if os.name == "nt":
            os.lseek(handle, 0, os.SEEK_SET)
            msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _open_lock_file(path, deadline):
    """Create/open the lock file, tolerating Windows' transient ACCESS_DENIED.

    Two processes calling ``O_CREAT`` on the same new path at the same instant
    can make one of them fail with ``PermissionError`` even though nothing holds
    the file; retrying is the documented workaround. Without this the lock
    itself becomes the failure, which is worse than no lock at all.
    """
    while True:
        try:
            return os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(POLL_SECONDS)


def _unlock(handle) -> None:
    if os.name == "nt":
        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle, fcntl.LOCK_UN)


class workspace_lock:
    """Exclusive across processes, re-entrant inside one process.

    ``with workspace_lock(path): ...`` blocks up to ``timeout`` seconds and then
    raises :class:`LockTimeout` rather than hanging a scheduler forever.
    """

    def __init__(self, path, *, timeout=DEFAULT_TIMEOUT):
        self.path = Path(path)
        self.timeout = float(timeout)
        self._key = os.path.abspath(str(self.path))
        self._acquired = False

    def __enter__(self):
        with _REGISTRY:
            held = _HELD.get(self._key)
            if held is not None:
                held[0] += 1
                self._acquired = True
                return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        # The handle is deliberately closed on release: on Windows an open
        # handle blocks deleting the workspace directory, which breaks every
        # caller that cleans up its temporary root.
        handle = _open_lock_file(self.path, deadline)
        if os.name == "nt" and os.fstat(handle).st_size == 0:
            os.write(handle, b"\0")
            os.lseek(handle, 0, os.SEEK_SET)
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                os.close(handle)
                raise LockTimeout(f"workspace lock busy: {self.path}")
            time.sleep(POLL_SECONDS)
        with _REGISTRY:
            _HELD[self._key] = [1, handle]
        self._acquired = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        if not self._acquired:
            return False
        with _REGISTRY:
            held = _HELD.get(self._key)
            if held is None:
                return False
            held[0] -= 1
            if held[0] > 0:
                return False
            _HELD.pop(self._key, None)
            handle = held[1]
        try:
            _unlock(handle)
        finally:
            os.close(handle)
        return False
