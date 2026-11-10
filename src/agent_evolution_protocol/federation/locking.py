"""Cross-process exclusive lock so two schedulers cannot lose each other's work.

The federation kernel does read-modify-write on ``state.json``. Without a lock
two schedulers both read the same snapshot, both append, and the second write
silently erases the first. The event log would still show both attempts, which
makes the damage hard to notice: the state and the audit trail disagree.

The lock is advisory and file based, so it works for any process that agrees to
take it, including a different language. It is re-entrant **within one thread**
so ``save()`` can be called while its caller already holds the workspace lock;
another thread in the same process must wait just like another process. A
process-wide re-entrancy shortcut is unsafe for session delivery: two scheduler
threads could otherwise enter the same original conversation before either has
persisted its ``SENDING`` fence.

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

#: path -> [depth, fd, owner_thread_id, acquiring]. The reservation represented
#: by ``acquiring`` prevents two threads in this process from both observing an
#: empty entry before either reaches the OS-level file lock.
_HELD: dict[str, list] = {}
_REGISTRY = threading.Condition(threading.Lock())


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
    """Exclusive across processes and threads, re-entrant for its owner thread.

    ``with workspace_lock(path): ...`` blocks up to ``timeout`` seconds and then
    raises :class:`LockTimeout` rather than hanging a scheduler forever.
    """

    def __init__(self, path, *, timeout=DEFAULT_TIMEOUT):
        self.path = Path(path)
        self.timeout = float(timeout)
        self._key = os.path.abspath(str(self.path))
        self._acquired = False

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        owner = threading.get_ident()
        with _REGISTRY:
            while True:
                held = _HELD.get(self._key)
                if held is None:
                    # Reserve the path before touching the OS lock. The
                    # underlying file lock is process-scoped on some hosts, so
                    # relying on it alone would still let a sibling thread in.
                    _HELD[self._key] = [1, None, owner, True]
                    break
                if held[2] == owner and not held[3]:
                    held[0] += 1
                    self._acquired = True
                    return self
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockTimeout(f"workspace lock busy: {self.path}")
                _REGISTRY.wait(timeout=min(POLL_SECONDS, remaining))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # The handle is deliberately closed on release: on Windows an open
            # handle blocks deleting the workspace directory, which breaks every
            # caller that cleans up its temporary root.
            handle = _open_lock_file(self.path, deadline)
            if os.name == "nt" and os.fstat(handle).st_size == 0:
                try:
                    os.write(handle, b"\0")
                    os.lseek(handle, 0, os.SEEK_SET)
                except PermissionError:
                    # Lost the race between fstat and write: another process
                    # created the first byte and already holds the region, so the
                    # write lands on a locked byte and Windows answers EACCES.
                    # The pre-fill is then redundant, not failed -- _try_lock
                    # below is what decides ownership.
                    pass
            while not _try_lock(handle):
                if time.monotonic() >= deadline:
                    os.close(handle)
                    raise LockTimeout(f"workspace lock busy: {self.path}")
                time.sleep(POLL_SECONDS)
        except BaseException:
            with _REGISTRY:
                held = _HELD.get(self._key)
                if held is not None and held[2] == owner and held[3]:
                    _HELD.pop(self._key, None)
                    _REGISTRY.notify_all()
            raise
        with _REGISTRY:
            held = _HELD.get(self._key)
            if held is None or held[2] != owner or not held[3]:
                try:
                    _unlock(handle)
                finally:
                    os.close(handle)
                raise RuntimeError(f"workspace lock reservation lost: {self.path}")
            held[1] = handle
            held[3] = False
            _REGISTRY.notify_all()
        self._acquired = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        if not self._acquired:
            return False
        with _REGISTRY:
            held = _HELD.get(self._key)
            if held is None:
                return False
            if held[2] != threading.get_ident():
                raise RuntimeError("workspace lock must be released by the owning thread")
            held[0] -= 1
            if held[0] > 0:
                return False
            _HELD.pop(self._key, None)
            handle = held[1]
        try:
            _unlock(handle)
        finally:
            os.close(handle)
            with _REGISTRY:
                _REGISTRY.notify_all()
        return False
