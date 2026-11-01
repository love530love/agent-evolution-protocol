"""R-08: ``ack_protocol.verdict()['sent_at']`` is the order's *deposit* time, not
the moment anything was typed.  The supervisor computes the wait from it, so an
order that sat in the queue and was sent a second ago is reported as "unacked
for three hours, over the limit" and an alert goes out claiming the agent has
been silent since then.

Run from the repository root:
  CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.tools/agent-runtime/Scripts/python.exe \
      docs/audit/repro/r08_sent_at_is_deposit_time.py
"""
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_evolution_protocol.coordination import ack_protocol, commander, hold_alerts
from agent_evolution_protocol.federation import supervisor


class _FakeAdapter:
    """Just enough of CommanderAdapter for _ack_blocks: it only calls ack_verdict."""

    def __init__(self, value):
        self._value = value

    def ack_verdict(self, task_id):
        return self._value


root = Path(tempfile.mkdtemp(prefix="aep-r08-"))
order = commander.submit_order(root, target="workbuddy", task="execute",
                               text="Please inspect the local inbox", sender="human")

# The order waited three hours for the client to open, then was typed just now.
order["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
order["session_id"] = "4242"
commander._record_attempt(root, order, state="MESSAGE_VISIBLE")

stored = commander._orders(root)[0]
typed_at = stored["attempts"][-1]["at"]
verdict = ack_protocol.verdict(stored, events=[])
print("order created_at (deposit)  =", stored["created_at"])
print("attempt at (actually typed) =", typed_at)
print("verdict['sent_at']          =", verdict["sent_at"])
print("equal to the deposit time   =", verdict["sent_at"] == stored["created_at"])

row = supervisor._ack_blocks(_FakeAdapter(verdict), "execute",
                             now=datetime.now(timezone.utc), hold_limit_seconds=3600)
print("held row                    =", json.dumps(row, ensure_ascii=False))
report = hold_alerts.emit(root, [{"task": "execute", **row}])
print("alerts                      =", json.dumps(report["alerts"], ensure_ascii=False))
for path in (root / "messages").rglob("*.json"):
    print("--- alert body ---")
    print(path.read_text(encoding="utf-8"))
print("root =", root)
