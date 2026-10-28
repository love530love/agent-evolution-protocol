"""Optional process heartbeat probe for Windows Agent clients."""
from __future__ import annotations
from typing import Any

def probe(pid: int) -> dict[str, Any]:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0: raise ValueError("pid must be positive")
    try:
        import psutil
    except ImportError:
        return {"status": "UNAVAILABLE", "reason": "install the optional windows extra"}
    try:
        process = psutil.Process(pid)
        with process.oneshot():
            return {"status": "ONLINE", "pid": pid, "name": process.name(), "create_time": process.create_time(), "memory_bytes": process.memory_info().rss}
    except psutil.NoSuchProcess:
        return {"status": "OFFLINE", "pid": pid}
    except psutil.AccessDenied:
        return {"status": "UNKNOWN", "pid": pid, "reason": "access denied"}
