"""Optional OCTO notification transport for an existing AEP event.

This module never creates a task or marks an agent as having accepted one.
Sending is explicit; uncertain writes are not retried.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess


def payload_for_event(event: dict, *, include_body: bool = False) -> dict:
    event_id = str(event.get("id", "")).strip()
    if not event_id or len(event_id) > 200:
        raise ValueError("event requires a bounded id")
    kind = str(event.get("kind") or event.get("type") or "event")[:80]
    target = str(event.get("to") or event.get("target") or "team")[:80]
    task = str(event.get("task") or event.get("topic") or "")[:80]
    content = f"[AEP {kind}] event={event_id} target={target} task={task}. Read the local AEP event, then ACK explicitly. OCTO delivery/read is not task acceptance."
    if include_body:
        body = str(event.get("text") or event.get("deliver_text") or event.get("instruction") or "")
        if len(body) > 8000:
            raise ValueError("event body exceeds 8000 characters")
        content += "\n" + body
    return {"type": 1, "content": content}


def send_event(event: dict, *, channel_id: str, channel_type: int = 1,
               include_body: bool = False, execute: bool = False,
               binary: str = "octo-cli", runner=subprocess.run) -> dict:
    if not channel_id or len(channel_id) > 200 or channel_type not in (1, 2):
        raise ValueError("channel_id and channel_type 1/2 are required")
    data = {"channel_id": channel_id, "channel_type": channel_type,
            "payload": payload_for_event(event, include_body=include_body)}
    if not execute:
        return {"state": "PREVIEW_ONLY", "event_id": event["id"], "request": data,
                "agent_accepted": False}
    resolved = shutil.which(binary)
    if not resolved:
        return {"state": "UNAVAILABLE", "event_id": event["id"],
                "reason": "octo-cli is not installed or not on PATH", "agent_accepted": False}
    command = [resolved, "message", "send", "--data",
               json.dumps(data, ensure_ascii=False, separators=(",", ":")),
               "--format", "json", "--no-retry", "--timeout", "15s"]
    try:
        result = runner(command, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"state": "UNKNOWN_DELIVERY", "event_id": event["id"],
                "reason": type(exc).__name__, "agent_accepted": False,
                "note": "Check OCTO message history before any manual resend."}
    if result.returncode != 0:
        return {"state": "UNKNOWN_DELIVERY", "event_id": event["id"],
                "exit_code": result.returncode, "agent_accepted": False,
                "note": "Check OCTO message history before any manual resend."}
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"state": "UNKNOWN_DELIVERY", "event_id": event["id"],
                "reason": "invalid JSON response", "agent_accepted": False}
    return {"state": "SENT_UNVERIFIED" if envelope.get("ok") is True else "UNKNOWN_DELIVERY",
            "event_id": event["id"], "octo_data": envelope.get("data") if envelope.get("ok") is True else None,
            "agent_accepted": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event_file", type=Path)
    parser.add_argument("--channel-id", required=True)
    parser.add_argument("--channel-type", type=int, default=1)
    parser.add_argument("--include-body", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Actually send one message; default is preview")
    args = parser.parse_args(argv)
    event = json.loads(args.event_file.read_text(encoding="utf-8"))
    receipt = send_event(event, channel_id=args.channel_id, channel_type=args.channel_type,
                         include_body=args.include_body, execute=args.execute)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt["state"] in {"PREVIEW_ONLY", "SENT_UNVERIFIED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
