"""告警落地链路：emit -> messages/<to>/ -> coord-status 的 unread -> coord-digest。

目的：确认「凌晨三点的人怎么知道要去看这个目录」这条路走不走得通。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

from agent_evolution_protocol.coordination import hold_alerts  # noqa: E402


def cli(*args):
    return subprocess.run([sys.executable, "-m", "agent_evolution_protocol", *args],
                          capture_output=True, text=True, encoding="utf-8",
                          env=dict(os.environ, PYTHONPATH=str(REPO / "src")))


def main():
    base = Path(tempfile.mkdtemp(prefix="aep-alert-"))
    coord = base / "coord"
    (coord / "commander" / "inbox").mkdir(parents=True)
    sent_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    order = {"id": "ORD-1", "target": "alpha", "task": "execute", "sender": "human",
             "created_at": sent_at, "state": "SENT", "event_id": "EVT-1",
             "text": "写 hello world"}
    (coord / "commander" / "inbox" / "ORD-1.json").write_text(
        json.dumps(order, ensure_ascii=False, indent=2), encoding="utf-8")

    held = [{"task": "execute", "over_limit": True, "unacked_for_seconds": 7200,
             "limit_seconds": 3600, "sent_at": sent_at, "event_id": "EVT-1",
             "state": "SENT", "verdict": "hold"}]
    rep = hold_alerts.emit(coord, held)
    print("--- hold_alerts.emit ---")
    print(json.dumps(rep, ensure_ascii=False, indent=2))

    print("\n--- where did the message land? ---")
    for p in sorted(coord.rglob("*.json")):
        print(f"  {p.relative_to(coord)}")

    print("\n--- the message body a person would read ---")
    for p in (coord / "messages").rglob("*.json"):
        body = json.loads(p.read_text(encoding="utf-8"))
        print(body["text"])
        print(f"[envelope] id={body['id']} to={body['to']} kind={body['kind']}")

    print("--- coord-status: does it surface the unread? ---")
    r = cli("--coord-root", str(coord), "coord-status")
    data = json.loads(r.stdout)
    print(f"exit={r.returncode}  unread={data['unread']}")
    print(f"tasks from coord-status: {len(data['tasks'])}")

    print("\n--- coord-digest --agent human ---")
    r = cli("--coord-root", str(coord), "coord-digest", "--agent", "human")
    print(f"exit={r.returncode}")
    print(r.stdout[:700] if r.returncode == 0 else r.stderr[:400])

    print("\n--- coord-digest --agent kernel (default recipient) ---")
    r = cli("--coord-root", str(coord), "coord-digest", "--agent", "kernel")
    print(f"exit={r.returncode} {r.stdout[:200]}")

    print("\n--- is there ONE command that lists unread across all mailboxes? ---")
    r = cli("--coord-root", str(coord), "coord-status")
    print("coord-status keys:", list(json.loads(r.stdout).keys()))
    shutil.rmtree(base, ignore_errors=True)


main()
