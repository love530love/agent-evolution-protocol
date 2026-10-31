"""A minimal A2A-style agent process, used to prove the adapter boundary is real.

It speaks newline-delimited JSON-RPC 2.0 on stdin/stdout and implements the four
calls the federation kernel needs: ``capabilities``, ``heartbeat``, ``deliver``
and ``observe``. Nothing else — no vendor SDK, no transcript parsing.

Run by ``federation.a2a.A2AAdapter``; also runnable by hand for inspection:

    python examples/a2a_agent_stub.py --agent alpha --capabilities code,review

Fault injection flags exist because a fake that cannot fail cannot test recovery:

    --outcome refused|failed|<signal>   what ``deliver`` reports
    --delay SECONDS                     sleep before answering (timeout tests)
    --die-after N                       exit after N requests (crash tests)
    --silent                            read requests but never answer
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
import time


def _now():
    return datetime.now(timezone.utc).isoformat()


def build_parser():
    parser = argparse.ArgumentParser(description="A2A loopback agent stub")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--capabilities", default="code")
    parser.add_argument("--outcome", default="accepted")
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--die-after", type=int, default=0)
    parser.add_argument("--silent", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    capabilities = sorted({item.strip() for item in args.capabilities.split(",") if item.strip()})
    served = 0
    deliveries = 0
    last_progress = _now()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        served += 1
        if args.die_after and served > args.die_after:
            sys.exit(17)
        if args.delay:
            time.sleep(args.delay)
        if args.silent:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "parse error"}}
            print(json.dumps(response), flush=True)
            continue

        method, params = request.get("method"), request.get("params") or {}
        last_progress = _now()
        if method == "capabilities":
            result = {"agent": args.agent, "capabilities": capabilities}
        elif method == "heartbeat":
            result = {"agent": args.agent, "alive": True, "last_progress_at": last_progress}
        elif method == "deliver":
            deliveries += 1
            if args.outcome in {"accepted", "refused", "failed"}:
                result = {"status": args.outcome, "signal": "",
                          "note": f"stub {args.outcome}"}
            else:
                result = {"status": "failed", "signal": args.outcome,
                          "note": f"stub signal {args.outcome}"}
        elif method == "observe":
            result = {"agent": args.agent, "state": "worked" if deliveries else "idle",
                      "deliveries": deliveries}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": request.get("id"),
                              "error": {"code": -32601, "message": f"no method {method}"}}),
                  flush=True)
            continue

        print(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}),
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
