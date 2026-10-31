"""A real, cross-process adapter: newline-delimited JSON-RPC over stdio.

``FakeAdapter`` proves the kernel's logic; it cannot prove that ``deliver`` and
``observe`` survive a process boundary, a silent peer, or a peer that dies
mid-conversation. This adapter does, and it deliberately reports those three
conditions as ordinary failures with named signals, so the kernel's diagnosis
table decides what to do instead of the adapter guessing.

The transport is A2A-shaped (JSON-RPC 2.0, one request per line) but says
nothing about A2A's HTTP binding; swapping in a real A2A client means replacing
:meth:`A2AAdapter._exchange` and nothing else.

A reader thread feeds responses into a queue because ``select`` does not work on
Windows pipes. A dead process puts a sentinel on the same queue, so a crash is
never mistaken for a timeout.
"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading


CRASHED = object()
TIMEOUT_SIGNAL = "adapter_timeout"
CRASH_SIGNAL = "adapter_crashed"
PROTOCOL_SIGNAL = "adapter_protocol_error"


class A2AAdapter:
    """Adapter for one agent process; restarts it after a crash on demand.

    ``command`` is the interpreter (default ``[sys.executable, "-u"]``);
    ``args`` is the script path plus its flags. Passing the interpreter again in
    ``args`` starts ``python -u python -u script.py``, which fails silently — the
    demo hit exactly that, so the split is stated here rather than inferred.
    """

    def __init__(self, agent, *, command=None, args=(), timeout=5.0, restart=True):
        if not isinstance(agent, str) or not agent.strip():
            raise ValueError("agent is required")
        self.agent = agent.strip()
        self.command = list(command or [sys.executable, "-u"])
        self.args = [str(item) for item in args]
        self.timeout = float(timeout)
        self.restart = bool(restart)
        self._process = None
        self._queue = None
        self._reader = None
        self._ids = 0
        self._capabilities = None
        self._last_progress_at = None
        self.crashes = 0

    # -- process lifecycle -------------------------------------------------
    def _start(self):
        self._process = subprocess.Popen(
            [*self.command, *self.args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1)
        self._queue = queue.Queue()
        self._reader = threading.Thread(target=self._pump, args=(self._process, self._queue),
                                        daemon=True)
        self._reader.start()

    @staticmethod
    def _pump(process, sink):
        try:
            for line in process.stdout:
                line = line.strip()
                if line:
                    sink.put(line)
        finally:
            sink.put(CRASHED)

    def _alive(self):
        return self._process is not None and self._process.poll() is None

    def _ensure(self):
        if self._alive():
            return True
        if self._process is not None:
            self.crashes += 1
            if not self.restart:
                return False
        self._start()
        return True

    def close(self):
        if self._process is None:
            return
        try:
            self._process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
        self._process = None

    def __enter__(self):
        self._ensure()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

    # -- transport ---------------------------------------------------------
    def _exchange(self, method, params=None):
        """Send one request, return ``(result, signal)``; never raises."""
        if not self._ensure():
            return None, CRASH_SIGNAL
        self._ids += 1
        request = {"jsonrpc": "2.0", "id": self._ids, "method": method,
                   "params": params or {}}
        try:
            self._process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
        except (OSError, ValueError):
            return None, CRASH_SIGNAL
        try:
            line = self._queue.get(timeout=self.timeout)
        except queue.Empty:
            return None, TIMEOUT_SIGNAL
        if line is CRASHED:
            return None, CRASH_SIGNAL
        try:
            reply = json.loads(line)
        except ValueError:
            return None, PROTOCOL_SIGNAL
        if not isinstance(reply, dict) or reply.get("id") != request["id"]:
            return None, PROTOCOL_SIGNAL
        if "error" in reply:
            return None, PROTOCOL_SIGNAL
        result = reply.get("result")
        if not isinstance(result, dict):
            return None, PROTOCOL_SIGNAL
        return result, ""

    # -- Adapter protocol --------------------------------------------------
    def capabilities(self):
        if self._capabilities is None:
            result, signal = self._exchange("capabilities")
            if signal:
                return set()
            self._capabilities = {str(item) for item in result.get("capabilities") or []}
        return set(self._capabilities)

    def heartbeat(self, now):
        result, signal = self._exchange("heartbeat")
        if signal:
            return {"agent": self.agent, "alive": False,
                    "last_progress_at": self._last_progress_at, "now": now, "signal": signal}
        self._last_progress_at = result.get("last_progress_at") or self._last_progress_at
        return {"agent": self.agent, "alive": bool(result.get("alive")),
                "last_progress_at": self._last_progress_at, "now": now}

    def deliver(self, task):
        result, signal = self._exchange("deliver", {"task": dict(task)})
        if signal:
            return {"status": "failed", "signal": signal,
                    "note": f"{signal} while delivering {task.get('id')}"}
        status = result.get("status")
        if status not in {"accepted", "refused", "failed"}:
            return {"status": "failed", "signal": PROTOCOL_SIGNAL,
                    "note": f"unknown status {status!r}"}
        return {"status": status, "signal": str(result.get("signal") or ""),
                "note": str(result.get("note") or "")}

    def observe(self):
        result, signal = self._exchange("observe")
        if signal:
            return {"agent": self.agent, "state": "unreachable", "signal": signal}
        return {"agent": self.agent, "state": str(result.get("state") or ""),
                "deliveries": int(result.get("deliveries") or 0)}
