"""Optional OpenTelemetry bridge with a dependency-free JSONL fallback."""
from __future__ import annotations
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

@contextmanager
def span(name: str, *, attributes: dict | None = None, log_path: Path | None = None):
    attrs = attributes or {}
    try:
        from opentelemetry import trace
        tracer = trace.get_tracer("agent-evolution-protocol")
        with tracer.start_as_current_span(name) as current:
            for key, value in attrs.items(): current.set_attribute(key, str(value))
            yield current
            return
    except ImportError:
        pass
    started = datetime.now(timezone.utc).isoformat()
    try:
        yield None
    finally:
        if log_path:
            path = Path(log_path); path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": "span", "name": name, "attributes": attrs, "started_at": started, "ended_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + "\n")
