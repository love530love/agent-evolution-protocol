"""R-03: ``transcript_bus.read_events(limit=N)`` returns the *oldest* N events,
not the newest.  Every caller passes it as "recent history", so once a
conversation has produced more than N events the newest ones -- including the
agent's ACK -- are never read, and the verdict says nobody answered.

Run from the repository root:
  CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.tools/agent-runtime/Scripts/python.exe \
      docs/audit/repro/r03_read_events_returns_oldest.py
"""
import tempfile
from pathlib import Path

from agent_evolution_protocol.coordination import ack_protocol, commander, transcript_bus

root = Path(tempfile.mkdtemp(prefix="aep-r03-"))
order = commander.submit_order(root, target="workbuddy", task="execute",
                               text="Please inspect the local inbox", sender="human")
order["session_id"] = "4242"
commander._record_attempt(root, order, state="MESSAGE_VISIBLE")

# 500 ordinary lines of conversation, attributed correctly so only the window
# is under test.
for index in range(500):
    transcript_bus.publish(root, agent="workbuddy", text=f"chatter {index}",
                           session_id="4242", role="assistant", direction="incoming")

# Then the answer.
ack = transcript_bus.publish(root, agent="workbuddy",
                             text="Done. AEP-ACK:" + order["event_id"],
                             session_id="4242", role="assistant", direction="incoming")
print("ack event id =", ack["event"]["id"])

events = transcript_bus.read_events(root, agent="workbuddy", limit=500)
print("events returned          =", len(events))
print("newest returned id       =", events[-1]["id"])
print("ack event is in the list =", any(row["id"] == ack["event"]["id"] for row in events))

verdict = ack_protocol.verdict(order, events=events)
print("verdict.state    =", verdict["state"])
print("verdict.accepted =", verdict["accepted"])
print("verdict.candidates =", verdict["candidates"])
print("check_acks       =", commander.check_acks(root)["acked"])

# There is no wider read: limit is capped at 500, so the newest events are
# unreachable from this API at all.
try:
    transcript_bus.read_events(root, agent="workbuddy", limit=501)
except ValueError as exc:
    print("limit=501 ->", type(exc).__name__, exc)

# Proof the reply is on disk and merely unread:
on_disk = sorted((root / "transcript_bus" / "events").glob("*.json"))
print("event files on disk      =", len(on_disk))
print("newest file on disk      =", on_disk[-1].stem)
print("root =", root)
