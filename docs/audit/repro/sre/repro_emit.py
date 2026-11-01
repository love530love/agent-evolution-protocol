"""S-08 / Q3 复现：信箱不可写时 hold_alerts 的返回，以及收件人如何回落。

两次对照，唯一差别是订单里有没有 event_id：
  有 event_id -> to=human, agent=alpha（能找到订单）
  无 event_id -> to=kernel, agent=unknown（找不到订单，静默回落到默认信箱）

再把 messages/ 设成不可写，看它是抛异常还是静默降级。
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


def build(sender, with_event_id):
    """``with_event_id`` 控制**订单**有没有 event_id；held row 永远带 ``EVT-1``。

    这正是 ``_find_order`` 失配的条件：held 行有 event_id，订单没有，于是
    ``order.get("event_id") != event_id`` 恒成立，订单匹配不上。
    """
    base = Path(tempfile.mkdtemp(prefix="aep-emit-"))
    coord = base / "coord"
    (coord / "commander" / "inbox").mkdir(parents=True)
    sent_at = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    order = {"id": "ORD-1", "target": "alpha", "task": "execute",
             "sender": sender, "created_at": sent_at}
    if with_event_id:
        order["event_id"] = "EVT-1"
    (coord / "commander" / "inbox" / "ORD-1.json").write_text(
        json.dumps(order, ensure_ascii=False), encoding="utf-8")
    held = [{"task": "execute", "over_limit": True, "unacked_for_seconds": 10800,
             "limit_seconds": 3600, "sent_at": sent_at,
             "event_id": "EVT-1", "state": "SENT"}]
    return base, coord, held


def main():
    for label, with_event_id in (("订单有 event_id", True), ("订单没有 event_id", False)):
        base, coord, held = build("human", with_event_id)
        rep = hold_alerts.emit(coord, held)
        first = (rep["alerts"] or rep["skipped"] or [{}])[0]
        print(f"--- {label} ---")
        print(f"  to={first.get('to')!r} agent={first.get('agent')!r} "
              f"order={first.get('order')!r} count={rep['count']}")
        shutil.rmtree(base, ignore_errors=True)

    print("\n--- 信箱目录不可写（模拟磁盘满） ---")
    base, coord, held = build("human", True)
    subprocess.run(["icacls", str(coord), "/deny",
                    f"{os.environ.get('USERNAME')}:(W,AD)"], capture_output=True)
    rep = hold_alerts.emit(coord, held)
    subprocess.run(["icacls", str(coord), "/remove:d", os.environ.get("USERNAME", "")],
                   capture_output=True)
    print(f"  没有抛异常，返回：{json.dumps(rep, ensure_ascii=False)[:300]}")
    print("  step() 会把这个放进 stdout 某一行的 alerts 字段，然后继续跑。")
    shutil.rmtree(base, ignore_errors=True)


main()
