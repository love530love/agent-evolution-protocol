"""一条任务从 running 走到「无法用 CLI 恢复的 blocked」的完整路径。

场景：只有一个 agent，它投递后不回话（真实世界最常见的情况）。
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

from agent_evolution_protocol.federation import engine, supervisor  # noqa: E402
from agent_evolution_protocol.federation.store import workspace_dir  # noqa: E402


def cli(*args):
    return subprocess.run([sys.executable, "-m", "agent_evolution_protocol", *args],
                          capture_output=True, text=True, encoding="utf-8",
                          env=dict(os.environ, PYTHONPATH=str(REPO / "src")))


def show(state, tag):
    rows = {k: (v["state"], v.get("note", "")) for k, v in state["tasks"].items()}
    print(f"  [{tag}] {rows}")


def main():
    base = Path(tempfile.mkdtemp(prefix="aep-stuck-"))
    coord = base / "coord"
    coord.mkdir()
    state = engine.create(base / "fed", goal="sre", acceptance=["ok"])
    ws = state["workspace_id"]
    engine.add_task(base / "fed", state, task_id="execute", requirement="do it",
                    capability="execute")
    engine.save(base / "fed", state)
    adapters = supervisor.build_adapters(coord_root=str(coord), agents=["alpha"])

    print("tick 1 (deliver):")
    supervisor.step(base / "fed", str(coord), ws, adapters, dispatch=False)
    show(engine.load(base / "fed", ws), "after tick 1")

    print("\ntick 2 (heartbeat_timeout=0 -> 认为 agent 卡住):")
    supervisor.step(base / "fed", str(coord), ws, adapters, dispatch=False,
                    heartbeat_timeout=0)
    state = engine.load(base / "fed", ws)
    show(state, "after tick 2")

    print("\n--- 现在用 CLI 恢复 ---")
    for task in ("execute",):
        r = cli("--coord-root", str(coord), "federate", "revive",
                "--federation-root", str(base / "fed"), "--workspace", ws,
                "--task", task, "--by", "sre", "--reason", "agent came back")
        print(f"revive {task}: exit={r.returncode}")
        print(f"  {(r.stdout or r.stderr).strip().splitlines()[-1][:200]}")

    print("\n--- 再 tick 一次，会不会自己走出来？ ---")
    supervisor.step(base / "fed", str(coord), ws, adapters, dispatch=False)
    show(engine.load(base / "fed", ws), "after tick 3")

    print("\n--- events 里怎么记的 ---")
    log = engine.EventLog(base / "fed", ws)
    for row in log.events()[-6:]:
        print(f"  seq={row['seq']:>3} {row['kind']:<18} "
              f"{json.dumps(row['payload'], ensure_ascii=False)[:110]}")
    shutil.rmtree(base, ignore_errors=True)


main()
