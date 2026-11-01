"""磁盘满时，事件日志和 state.json 会不会分叉。

先让 tick 在写事件之后、save 之前失败，再看两边说了什么。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

from agent_evolution_protocol.federation import engine  # noqa: E402
from agent_evolution_protocol.federation.store import EventLog, workspace_dir  # noqa: E402


def main():
    base = Path(tempfile.mkdtemp(prefix="aep-diverge-"))
    (base / "coord").mkdir()
    (base / "fed").mkdir()
    state = engine.create(base / "fed", goal="sre", acceptance=["ok"])
    ws = state["workspace_id"]
    engine.add_task(base / "fed", state, task_id="t0000",
                    requirement="do it", capability="execute")
    engine.save(base / "fed", state)
    wdir = workspace_dir(base / "fed", ws)

    log = EventLog(base / "fed", ws)
    before_events = len(log.events())
    before_state = json.loads((wdir / "state.json").read_text(encoding="utf-8"))
    print(f"before: events={before_events} state.t0000={before_state['tasks']['t0000']['state']}")

    subprocess.run(["icacls", str(wdir), "/deny", f"{os.environ.get('USERNAME')}:(W,AD)"],
                   capture_output=True)
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    r = subprocess.run([sys.executable, "-m", "agent_evolution_protocol",
                        "--coord-root", str(base / "coord"), "federate", "serve",
                        "--federation-root", str(base / "fed"), "--workspace", ws,
                        "--agents", "alpha", "--no-dispatch", "--max-ticks", "1"],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    print(f"serve exit={r.returncode}")
    print("last stderr line:", (r.stderr or "").strip().splitlines()[-1][:160])
    subprocess.run(["icacls", str(wdir), "/remove:d", os.environ.get("USERNAME", "")],
                   capture_output=True)

    after_events = log.events()
    after_state = json.loads((wdir / "state.json").read_text(encoding="utf-8"))
    print(f"after : events={len(after_events)} state.t0000={after_state['tasks']['t0000']['state']}")
    print("\n--- events appended during the failed tick ---")
    for row in after_events[before_events:]:
        print(f"  seq={row['seq']} kind={row['kind']} payload={json.dumps(row['payload'], ensure_ascii=False)[:120]}")

    print("\n--- chain still valid? ---")
    print(log.verify())
    print("\n--- orders actually written to the inbox ---")
    inbox = base / "coord" / "commander" / "inbox"
    print(sorted(p.name for p in inbox.glob("*.json")) if inbox.exists() else "none")

    print("\n--- restart: does it re-dispatch the same task? ---")
    r = subprocess.run([sys.executable, "-m", "agent_evolution_protocol",
                        "--coord-root", str(base / "coord"), "federate", "serve",
                        "--federation-root", str(base / "fed"), "--workspace", ws,
                        "--agents", "alpha", "--no-dispatch", "--max-ticks", "1"],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    print(f"exit={r.returncode}")
    print((r.stdout or "")[:600])
    print("events now:", len(log.events()))
    for row in log.events()[len(after_events):]:
        print(f"  seq={row['seq']} kind={row['kind']} payload={json.dumps(row['payload'], ensure_ascii=False)[:120]}")
    shutil.rmtree(base, ignore_errors=True)


main()
