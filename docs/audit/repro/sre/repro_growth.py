"""Q1 复现：serve 连跑 7 天，哪一处先耗尽。

测量三拍的真实耗时（第 2 拍才是稳态：第 1 拍只是首次投递），以及各目录单价。

用法：
    PYTHONPATH=src python docs/audit/repro/sre/repro_growth.py 100
    PYTHONPATH=src python docs/audit/repro/sre/repro_growth.py 200

200 任务时第 3 拍会超过 6 分钟，脚本默认只跑两拍；用 --ticks 改。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

from agent_evolution_protocol.federation import engine  # noqa: E402
from agent_evolution_protocol.federation.store import EventLog, workspace_dir  # noqa: E402


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    ticks = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    base = Path(tempfile.mkdtemp(prefix=f"aep-growth-{n}-"))
    (base / "coord").mkdir()
    (base / "fed").mkdir()
    state = engine.create(base / "fed", goal="sre", acceptance=["ok"])
    ws = state["workspace_id"]
    for i in range(n):
        engine.add_task(base / "fed", state, task_id=f"t{i:04d}",
                        requirement=f"task {i}", capability="execute")
    engine.save(base / "fed", state)

    log = EventLog(base / "fed", ws)
    wdir = workspace_dir(base / "fed", ws)
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))

    print(f"N={n}, interval default 30s")
    for i in range(1, ticks + 1):
        prev = len(log.events())
        t0 = time.perf_counter()
        r = subprocess.run(
            [sys.executable, "-m", "agent_evolution_protocol",
             "--coord-root", str(base / "coord"), "federate", "serve",
             "--federation-root", str(base / "fed"), "--workspace", ws,
             "--agents", "alpha", "--no-dispatch", "--max-ticks", "1"],
            capture_output=True, text=True, encoding="utf-8", env=env)
        dt = time.perf_counter() - t0
        now = len(log.events())
        print(f"  tick {i}: {dt:6.2f}s  exit={r.returncode}  "
              f"stdout={len(r.stdout):6d}B  events +{now - prev}  "
              f"({dt / 30:.1f}x over the 30s interval)", flush=True)

    size = (wdir / "events.jsonl").stat().st_size
    total = len(log.events())
    print(f"\n  events.jsonl : {total} events, {size} B -> {size / total:.0f} B/event")
    for name in ("inbox", "state"):
        folder = base / "coord" / "commander" / name
        files = list(folder.glob("*.json")) if folder.exists() else []
        if files:
            tot = sum(f.stat().st_size for f in files)
            print(f"  commander/{name:<6}: {len(files)} files, {tot} B -> {tot / len(files):.0f} B/file")
    st = (wdir / "state.json").stat().st_size
    print(f"  state.json   : {st} B for {n} tasks -> {st / n:.0f} B/task")

    ticks_per_day = min(86400 / 30, 86400 / max(dt, 1))
    per_day = len(r.stdout) * ticks_per_day / 1024 / 1024
    print(f"\n  按最后一拍的 stdout 外推：{ticks_per_day:.0f} 拍/天 -> {per_day:.1f} MiB/天"
          f" -> 7 天 {per_day * 7:.0f} MiB")
    shutil.rmtree(base, ignore_errors=True)


main()
