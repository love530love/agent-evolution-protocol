"""心跳从哪来：是 agent 的进展，还是 order 文件的 updated_at？

结论决定一件事：一个永远投不进去的单（DEFERRED 重试）会不会让任务永远停在 running。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

from agent_evolution_protocol.federation import engine, supervisor  # noqa: E402


def main():
    base = Path(tempfile.mkdtemp(prefix="aep-hb-"))
    coord = base / "coord"
    (coord / "commander" / "inbox").mkdir(parents=True)
    state = engine.create(base / "fed", goal="sre", acceptance=["ok"])
    ws = state["workspace_id"]
    engine.add_task(base / "fed", state, task_id="execute", requirement="do it",
                    capability="execute")
    engine.save(base / "fed", state)
    adapters = supervisor.build_adapters(coord_root=str(coord), agents=["alpha"])
    adapter = adapters[0]

    old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    order = {"id": "ORD-1", "target": "alpha", "task": "execute", "sender": "kernel",
             "created_at": old, "updated_at": old, "state": "DEPOSITED",
             "event_id": "EVT-1", "text": "do it"}
    path = coord / "commander" / "inbox" / "ORD-1.json"
    path.write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")

    st = engine.load(base / "fed", ws)
    st["tasks"]["execute"].update({"state": "running", "owner": "alpha", "attempts": 1})
    engine.save(base / "fed", st)

    print("order updated_at =", old, "(5 小时前)")
    print("heartbeat ->", adapter.heartbeat(datetime.now(timezone.utc)))
    print("stalled(900s) ->", engine._stalled(adapter, datetime.now(timezone.utc), 900))

    print("\n现在只改 order 的 updated_at，agent 什么都没做：")
    order["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")
    print("heartbeat ->", adapter.heartbeat(datetime.now(timezone.utc)))
    print("stalled(900s) ->", engine._stalled(adapter, datetime.now(timezone.utc), 900))

    print("\n结论：把 updated_at 每拍刷新一次，任务就永远不会被判为 stalled。")
    shutil.rmtree(base, ignore_errors=True)


main()
