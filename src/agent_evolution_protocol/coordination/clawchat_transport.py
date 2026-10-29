"""Optional ClawChat/Cowchat NDJSON transport.

The adapter is intentionally opt-in and dependency-free. It never connects
unless ``execute=True`` is supplied by the caller.
"""
from __future__ import annotations

import json
import socket
import os
from pathlib import Path
from urllib.request import Request, urlopen
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ClawChatConfig:
    host: str = "127.0.0.1"
    port: int = 0
    room: str = "workspace"
    timeout: float = 5.0
    discovery: str = ""
    session_id: str = ""
    token: str = ""


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


def _discover_raw(path: str = "") -> dict[str, Any]:
    """Read ClawChat's ephemeral discovery record without exposing its token."""
    candidates = [path] if path else [
        os.path.join(os.environ.get("APPDATA", ""), "com.newbaselab", "clawchat", "machine_channel.json"),
        os.path.join(os.environ.get("APPDATA", ""), "clawchat", "machine_channel.json"),
    ]
    for raw in candidates:
        if raw and Path(raw).is_file():
            try:
                data = json.loads(Path(raw).read_text(encoding="utf-8"))
                if int(data.get("version", 0)) != 1:
                    return {"status": "UNSUPPORTED_VERSION", "version": data.get("version")}
                return {"status": "DISCOVERED", "path": raw, "port": int(data["port"]),
                        "token": str(data["token"]), "app_version": data.get("app_version"),
                        "roster": data.get("roster", [])}
            except (OSError, ValueError, KeyError) as exc:
                return {"status": "DISCOVERY_ERROR", "error": str(exc)}
    return {"status": "NOT_RUNNING"}


def discover(path: str = "") -> dict[str, Any]:
    """Public discovery view; deliberately redacts the bearer token."""
    data = _discover_raw(path)
    if "token" in data:
        data = {k: v for k, v in data.items() if k != "token"}
    return data


def register(config: ClawChatConfig, *, agent: str, name: str, cwd: str,
             native: str = "", pid: int = 0, connect_code: str = "") -> dict[str, Any]:
    """Register one machine-channel session; one call only, no polling."""
    d = _discover_raw(config.discovery)
    if d.get("status") != "DISCOVERED":
        return d
    payload: dict[str, Any] = {"pid": pid or os.getpid(), "cwd": cwd, "agent": agent, "name": name}
    if native: payload["native"] = native
    if connect_code: payload["connect_code"] = connect_code
    result = _post(d, "/v1/session", payload, config.timeout)
    if result.get("session_id"):
        result["status"] = "REGISTERED"
    return result


def send_http(config: ClawChatConfig, *, text: str, agent: str = "", execute: bool = False) -> dict[str, Any]:
    """Send through the ClawChat machine channel with explicit execute gating."""
    if not execute:
        return {"status": "PREVIEW", "transport": "clawchat-http", "text": text}
    d = _discover_raw(config.discovery)
    if d.get("status") != "DISCOVERED": return d
    payload = {"session_id": config.session_id, "text": text}
    if agent: payload["agent"] = agent
    return _post(d, "/v1/send", payload, config.timeout)


def _post(discovery: dict[str, Any], path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(f"http://127.0.0.1:{discovery['port']}{path}", data=body, method="POST",
                  headers={"Authorization": f"Bearer {discovery['token']}", "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
        try: return json.loads(raw)
        except json.JSONDecodeError: return {"status": "INVALID_RESPONSE", "raw": raw[:1000]}
    except Exception as exc:  # transport errors are returned, never retried here
        return {"status": "TRANSPORT_ERROR", "error": str(exc)}
