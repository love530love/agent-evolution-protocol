"""R-01: the production perception path stamps every line role=unknown, so the
ACK detector can never accept the agent's reply -- and the receipt says the task
"advanced without an acknowledgement" while the acknowledgement is on disk.

Reproduces the two call sites verbatim:
  commander.perceive -> bus.publish(root, agent, text, source, session_id, window_title)
  supervisor._settle  -> commander.check_acks -> ack_protocol.verdict

Run from the repository root:
  CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.tools/agent-runtime/Scripts/python.exe \
      docs/audit/repro/r01_ack_role_never_set.py
"""
import json
import tempfile
from pathlib import Path

from agent_evolution_protocol.coordination import ack_protocol, commander, transcript_bus

root = Path(tempfile.mkdtemp(prefix="aep-r01-"))
order = commander.submit_order(root, target="workbuddy", task="execute",
                               text="Please inspect the local inbox", sender="human")
order["session_id"] = "4242"
commander._record_attempt(root, order, state="MESSAGE_VISIBLE")

# --- exactly what commander.perceive() does (commander.py:443-445) ----------
# role= and direction= are NOT passed, so transcript_bus defaults apply.
published = transcript_bus.publish(
    root, agent="workbuddy",
    text="Done. AEP-ACK:" + order["event_id"],
    source="screen-ocr",
    session_id="4242",
    window_title="WorkBuddy",
)
event = published["event"]
print("published event role      =", repr(event["role"]))
print("published event direction =", repr(event["direction"]))

verdict = ack_protocol.verdict(order, events=transcript_bus.read_events(
    root, agent="workbuddy", limit=500))
print("verdict.state   =", verdict["state"])
print("verdict.accepted=", verdict["accepted"])
print("verdict.reason  =", verdict["reason"])
print("verdict.rejected=", json.dumps(verdict["rejected"], ensure_ascii=False))

print("check_acks      =", json.dumps(commander.check_acks(root)["acked"]))
print("order.state     =", commander._orders(root)[0]["state"])

# The supervisor's own row for the same order, under the default policy.
from agent_evolution_protocol.coordination import result_readback
observed = result_readback.observe(transcript_bus.read_events(root, agent="workbuddy", limit=500),
                                   session_id="4242")
print("readback.session_reason =", observed["session_reason"])
print("readback.attributable   =", observed["attributable"])
print("readback.rejected       =", json.dumps(observed["rejected"], ensure_ascii=False))

# For contrast: the same reply, published the way the tests publish it.
transcript_bus.publish(root, agent="workbuddy",
                       text="Confirmed AEP-ACK:" + order["event_id"],
                       session_id="4242", role="assistant", direction="incoming")
print("with role/direction set, verdict.state =",
      ack_protocol.verdict(order, events=transcript_bus.read_events(
          root, agent="workbuddy", limit=500))["state"])
print("root =", root)
