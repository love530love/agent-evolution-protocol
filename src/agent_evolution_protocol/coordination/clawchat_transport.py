"""Optional ClawChat/Cowchat NDJSON transport.

The adapter is intentionally opt-in and dependency-free. It never connects
unless ``execute=True`` is supplied by the caller.
"""
from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ClawChatConfig:
    host: str = "127.0.0.1"
    port: int = 0
    room: str = "workspace"
    timeout: float = 5.0


def envelope(*, event_id: str, sender: str, target: str, text: str,
             room: str = "workspace", task: str = "") -> dict[str, Any]:
    return {
        "type": "message",
        "event_id": event_id,
        "room": room,
        "sender": sender,
        "target": target,
        "task": task,
        "text": text,
    }


def send(config: ClawChatConfig, message: dict[str, Any], *, execute: bool = False) -> dict[str, Any]:
    """Preview or send one NDJSON message. No implicit retry or reconnect."""
    body = (json.dumps(message, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    if not execute:
        return {"status": "PREVIEW", "transport": "clawchat", "bytes": len(body), "message": message}
    if not config.port:
        return {"status": "CONFIG_REQUIRED", "transport": "clawchat", "error": "port is required"}
    try:
        with socket.create_connection((config.host, config.port), timeout=config.timeout) as conn:
            conn.sendall(body)
            conn.shutdown(socket.SHUT_WR)
            response = conn.recv(1024 * 1024).decode("utf-8", "replace")
        return {"status": "SENT", "transport": "clawchat", "response": response, "event_id": message.get("event_id")}
    except OSError as exc:
        return {"status": "TRANSPORT_ERROR", "transport": "clawchat", "error": str(exc), "event_id": message.get("event_id")}
