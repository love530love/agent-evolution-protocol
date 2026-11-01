"""R-09: a torn last line in ``events.jsonl`` (kill -9 mid-append) makes
``EventLog.verify()`` raise ``JSONDecodeError`` instead of returning the report
its docstring promises ("Return a report instead of raising, so callers can
decide"), and makes every later ``append()`` fail too -- so the workspace cannot
record anything again, and ``recover()`` crash-loops a supervisor.

Run from the repository root:
  CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.tools/agent-runtime/Scripts/python.exe \
      docs/audit/repro/r09_torn_event_log.py
"""
import json
import tempfile
from pathlib import Path

from agent_evolution_protocol.federation import engine, store

root = Path(tempfile.mkdtemp(prefix="aep-r09-"))
state = engine.create(root, goal="demo", acceptance=["it runs"])
wid = state["workspace_id"]
log = store.EventLog(root, wid)
log.append("note", actor="kernel", payload={"n": 1})

path = log.path
before = path.read_text(encoding="utf-8")
# A process killed during write(2) leaves a prefix of one line.  Simulate it.
path.write_text(before + '{"schema": "aep-event-envelope-v1", "seq": 3, "at": "2026', encoding="utf-8")

for label, call in (("verify", lambda: log.verify()),
                    ("append", lambda: log.append("note", actor="kernel")),
                    ("recover", lambda: engine.recover(root, wid))):
    try:
        result = call()
        print(f"{label}: returned {json.dumps(result, default=str)[:120]}")
    except Exception as exc:  # noqa: BLE001 - the point is which exception escapes
        print(f"{label}: raised {type(exc).__name__}: {exc}")

print("bytes now on disk =", len(path.read_bytes()))
print("root =", root)
