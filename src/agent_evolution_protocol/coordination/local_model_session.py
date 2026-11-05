"""Ephemeral local model session: load on demand, always unload on exit.

Operator constraints (2026-11-05): the project must not slow the machine down,
the default capability path must not need any model at all, and a model should
be "use it and let it go" rather than a resident process.

Therefore this module:
  * is never imported by any rule/decision path;
  * never starts a server (`lms server start` would create a resident service);
  * loads with `--ttl`, so even a crashed caller cannot leave a model resident;
  * uses `--gpu off` and a small context by default;
  * unloads by exact `--identifier`, never `--all` (other people's models stay);
  * unloads in `finally`, and never masks the original exception.

No polling, no watcher, no background thread. If the backend is unavailable,
callers degrade to the rule-only result instead of starting anything.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import subprocess

DEFAULT_MODEL_KEY = "qwen3-0.6b"
DEFAULT_IDENTIFIER = "aep-insight"
DEFAULT_TTL_SECONDS = 60
DEFAULT_CONTEXT_LENGTH = 2048
DEFAULT_GPU = "off"


def lms_binary() -> str:
    return os.environ.get("AEP_LMS_BIN", "lms")


def ollama_binary() -> str:
    return os.environ.get("AEP_OLLAMA_BIN", "ollama")


@dataclass(frozen=True)
class Plan:
    backend: str = "lmstudio"
    model: str = DEFAULT_MODEL_KEY
    identifier: str = DEFAULT_IDENTIFIER
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    gpu: str = DEFAULT_GPU
    context_length: int = DEFAULT_CONTEXT_LENGTH

    def __post_init__(self):
        if self.backend not in {"lmstudio", "ollama"}:
            raise ValueError("backend must be lmstudio or ollama")
        if not self.model.strip() or not self.identifier.strip():
            raise ValueError("model and identifier required")
        if not 10 <= self.ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be 10..3600")
        if not 512 <= self.context_length <= 8192:
            raise ValueError("context_length must be 512..8192")
        if self.gpu not in {"off", "max"} and not self.gpu.replace(".", "", 1).isdigit():
            raise ValueError("gpu must be off, max, or a numeric ratio")

    def commands(self) -> dict:
        """argv lists only — never a shell string, never `server start`."""
        if self.backend == "lmstudio":
            return {
                "load": [lms_binary(), "load", self.model,
                         "--ttl", str(self.ttl_seconds), "--gpu", self.gpu,
                         "-c", str(self.context_length), "-y",
                         "--identifier", self.identifier],
                "unload": [lms_binary(), "unload", self.identifier],
            }
        # Ollama loads on first request; there is nothing resident to start.
        return {"load": None, "unload": [ollama_binary(), "stop", self.model]}


class ModelUnavailable(RuntimeError):
    pass


def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(argv, shell=False, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          check=False)


class EphemeralModelSession:
    """Context manager. `runner` and `probe` are injectable for tests."""

    def __init__(self, plan: Plan | None = None, *, runner=None, probe=None,
                 timeout: float = 20.0):
        self.plan = plan or Plan()
        self.runner = runner or (lambda argv: _run(argv, timeout))
        self.probe = probe
        self.loaded = False
        self.commands_run: list[list[str]] = []

    def __enter__(self):
        if self.probe is not None and not self.probe():
            raise ModelUnavailable("lm-studio-server-off; not starting a resident service")
        load = self.plan.commands()["load"]
        if load is not None:
            result = self.runner(load)
            self.commands_run.append(load)
            if getattr(result, "returncode", 1) != 0:
                raise ModelUnavailable(f"model-load-failed:{getattr(result, 'returncode', '?')}")
        self.loaded = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self.loaded:
            return False
        unload = self.plan.commands()["unload"]
        try:
            self.runner(unload)
            self.commands_run.append(unload)
        except Exception:
            # Never mask the caller's exception; --ttl is the second guard.
            pass
        finally:
            self.loaded = False
        return False
