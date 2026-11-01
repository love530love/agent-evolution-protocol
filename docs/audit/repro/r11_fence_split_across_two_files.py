"""R-11: the "durable fence against process death" is written to two files in
sequence (``commander/state/<id>.json`` first, then
``commander/inbox/<id>.json``) and only the *inbox* copy is ever read back.  A
kill between the two writes loses the fence from the file the system reads, so
the order still looks freshly deposited and the next tick types it again.

dispatch() says: "durably fence process death before touching
discovery/launch/transport.  A later caller must not replay a send just because
the final adapter receipt was never written."

Run from the repository root:
  CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.tools/agent-runtime/Scripts/python.exe \
      docs/audit/repro/r11_fence_split_across_two_files.py
"""
import json
import tempfile
from pathlib import Path

from agent_evolution_protocol.coordination import commander

root = Path(tempfile.mkdtemp(prefix="aep-r11-"))
order = commander.submit_order(root, target="workbuddy", task="execute",
                               text="Please inspect the local inbox", sender="human")

# What _mark_dispatching does, killed after its first _atomic:
fenced = dict(order, state="DISPATCHING")
commander._atomic(commander._state_path(root, order["id"]), fenced)
# ... process dies here, before _atomic(inbox) runs.

inbox_copy = json.loads((root / "commander" / "inbox" / f"{order['id']}.json").read_text(encoding="utf-8"))
state_copy = json.loads((root / "commander" / "state" / f"{order['id']}.json").read_text(encoding="utf-8"))
print("inbox state after the kill =", inbox_copy["state"])
print("state/ state after the kill=", state_copy["state"])
print("_preinput_retryable(inbox) =", commander._preinput_retryable(inbox_copy))

calls = []
commander._dispatch = lambda root, *, order: (calls.append(order["id"]),
                                              {"state": "MESSAGE_VISIBLE"})[1]
result = commander.dispatch(root, order=inbox_copy)
print("dispatch() returned        =", result["state"])
print("_dispatch was invoked      =", calls == [order["id"]], calls)
print("root =", root)
