"""R-02: a task whose agent really did answer is completed as
"released_unacked", and its receipt says the reason was "not_assistant".

The transcript holds the agent's reply.  Nothing in the receipt says so.

Run from the repository root:
  CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.tools/agent-runtime/Scripts/python.exe \
      docs/audit/repro/r02_receipt_says_unacked.py
"""
import json
import tempfile
from pathlib import Path

from agent_evolution_protocol.coordination import commander, transcript_bus
from agent_evolution_protocol.federation import engine, intake, supervisor

base = Path(tempfile.mkdtemp(prefix="aep-r02-"))
root, coord = base / "state", str(base / "coordination")

receipt = intake.intake(root, coord, text="跑通 README", agents=["alpha", "beta"],
                        shape="single", sender="kernel")
adapters = supervisor.build_adapters(coord_root=coord, agents=["alpha", "beta"],
                                     workspace_id=receipt["workspace"])
order = commander._orders(coord)[0]
order["session_id"] = "4242"
commander._record_attempt(coord, order, state="MESSAGE_VISIBLE")

# The agent answers, through the perception path the product actually uses.
transcript_bus.publish(coord, agent="alpha",
                       text="收到。AEP-ACK:" + order["event_id"],
                       source="screen-ocr", session_id="4242", window_title="Qoder")

# The agent also writes back phase=done, as the assignment tells it to.
folder = Path(coord) / "task_states"
folder.mkdir(parents=True, exist_ok=True)
(folder / "execute.json").write_text(json.dumps(
    {"task": "execute", "owner": "alpha", "phase": "done", "evidence": []}),
    encoding="utf-8")

result = supervisor.step(root, coord, receipt["workspace"], adapters, dispatch=False)
print(json.dumps({"closed": result["closed"], "held": result["held"],
                  "released": result["released"]}, ensure_ascii=False, indent=1))
print("workspace task state =", result["tasks"]["execute"]["state"])

# And the same reply, published with the attribution the tests use:
transcript_bus.publish(coord, agent="alpha",
                       text="再次确认 AEP-ACK:" + order["event_id"],
                       session_id="4242", role="assistant", direction="incoming")
print("after a role-attributed copy:",
      json.dumps(commander.check_acks(coord)["acked"], ensure_ascii=False))
print("base =", base)
