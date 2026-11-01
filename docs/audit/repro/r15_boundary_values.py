"""Two boundary values that escape the exception lists guarding the heartbeat.

1. ``hold_limit_seconds=float('inf')`` -- the obvious way to say "never alert on
   a limit" -- reaches ``hold_alerts._seconds()``, which calls ``int(inf)``.
   ``OverflowError`` is not in the tuple ``supervisor.step`` catches, so one
   boundary value takes the whole step down.  ``float('nan')`` is handled
   gracefully, which is what makes the ``inf`` gap look like an oversight rather
   than a decision.
2. An order whose ``expires_at`` has no UTC offset (``datetime.fromisoformat``
   parses it happily) makes ``commander.tick`` compare a naive datetime with an
   aware one.  ``TypeError`` is not caught either, and ``supervisor.step`` only
   catches ``(OSError, ValueError)`` around ``commander.tick``.

Run from the repository root:
  CODEBUDDY_SAFE_DELETE_ENABLED=0 ./.tools/agent-runtime/Scripts/python.exe \
      docs/audit/repro/r15_boundary_values.py
"""
import json
import tempfile
from pathlib import Path

from agent_evolution_protocol.coordination import commander, hold_alerts

root = Path(tempfile.mkdtemp(prefix="aep-r15-"))

print("--- 1. hold_limit_seconds ---")
for limit in (3600, 0, float("nan"), float("inf")):
    row = {"task": "execute", "over_limit": False, "limit_seconds": limit,
           "unacked_for_seconds": 5, "sent_at": "2026-11-01T00:00:00+00:00"}
    try:
        report = hold_alerts.emit(root, [row])
        print(f"limit={limit!r:8} -> waiting={report['waiting']} skipped={report['skipped']}")
    except Exception as exc:  # noqa: BLE001
        print(f"limit={limit!r:8} -> raised {type(exc).__name__}: {exc}")

print("--- 2. naive expires_at ---")
order = commander.submit_order(root, target="workbuddy", task="execute", text="x",
                               sender="human")
path = root / "commander" / "inbox" / f"{order['id']}.json"
payload = json.loads(path.read_text(encoding="utf-8"))
payload["expires_at"] = "2030-01-01T00:00:00"          # parsed, but no offset
path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
try:
    commander.tick(root, perceive_first=False)
    print("tick returned normally")
except Exception as exc:  # noqa: BLE001
    print(f"commander.tick raised {type(exc).__name__}: {exc}")
print("root =", root)
