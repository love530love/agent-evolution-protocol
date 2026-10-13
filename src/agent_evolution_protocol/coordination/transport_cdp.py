"""Chrome DevTools Protocol transport for Electron agent clients.

Electron clients such as WorkBuddy and Qoder expose no accessibility tree, so
UIA sees an empty window and OCR is the only fallback. When a client runs with
``--remote-debugging-port``, CDP gives us the real DOM instead: exact transcript
text, a real composer element, and a read-back we can trust.

This is the preferred transport for Electron clients and the only one that can
both read a full conversation and type into the composer with proof. It never
launches a client by itself; launching stays in ``client_registry.launch``.

Standard library only: a minimal WebSocket client is implemented below so the
protocol keeps working in an offline, dependency-free checkout.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import time
import urllib.request
from uuid import uuid4

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DEFAULT_PORTS = list(range(9222, 9242))


class CdpError(RuntimeError):
    pass


class _WebSocket:
    """Minimal RFC 6455 client: text frames, masking, fragmented reads."""

    def __init__(self, url: str, timeout: float = 15.0):
        if not url.startswith("ws://"):
            raise CdpError(f"unsupported websocket url: {url}")
        rest = url[len("ws://"):]
        hostport, _, path = rest.partition("/")
        host, _, port_text = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port_text or 80)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(handshake.encode())
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CdpError("websocket handshake closed")
            buffer += chunk
        header = buffer.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        if "101" not in header.splitlines()[0]:
            raise CdpError("websocket upgrade rejected: " + header.splitlines()[0])
        self._buffer = buffer.split(b"\r\n\r\n", 1)[1]
        self._id = 0

    def _recv(self, size: int) -> bytes:
        while len(self._buffer) < size:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CdpError("websocket closed mid-frame")
            self._buffer += chunk
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    def send_text(self, payload: str) -> None:
        data = payload.encode("utf-8")
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def recv_text(self) -> str:
        while True:
            first, second = self._recv(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv(8))[0]
            payload = self._recv(length) if length else b""
            if opcode == 0x8:
                raise CdpError("websocket closed by peer")
            if opcode in (0x1, 0x2):
                return payload.decode("utf-8", "ignore")
            if opcode == 0x9:
                continue

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _http_json(url: str, timeout: float = 3.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception:
        return None


def probe(port: int, timeout: float = 1.5) -> dict:
    version = _http_json(f"http://127.0.0.1:{port}/json/version", timeout)
    if not version:
        return {"port": port, "open": False}
    return {"port": port, "open": True, "browser": version.get("Browser", ""),
            "protocol": version.get("Protocol-Version", "")}


def scan(ports: list[int] | None = None, timeout: float = 0.6) -> list[dict]:
    found = []
    for port in ports or DEFAULT_PORTS:
        hit = probe(port, timeout)
        if hit.get("open"):
            hit["targets"] = targets(port)
            found.append(hit)
    return found


def targets(port: int) -> list[dict]:
    data = _http_json(f"http://127.0.0.1:{port}/json/list") or []
    return [{"id": item.get("id"), "type": item.get("type"), "title": item.get("title", ""),
             "url": item.get("url", ""), "ws": item.get("webSocketDebuggerUrl", "")}
            for item in data if item.get("type") == "page"]


def pick_target(port: int, *, title_contains: str = "") -> dict:
    rows = targets(port)
    if not rows:
        raise CdpError(f"no page target on port {port}")
    if title_contains:
        lowered = title_contains.lower()
        for row in rows:
            if lowered in row["title"].lower():
                return row
    return rows[0]


def connect(ws_url: str, timeout: float = 15.0) -> _WebSocket:
    return _WebSocket(ws_url, timeout=timeout)


def evaluate(ws: _WebSocket, expression: str, *, await_promise: bool = False,
             timeout: float = 20.0) -> dict:
    ws._id += 1
    ws.send_text(json.dumps({
        "id": ws._id, "method": "Runtime.evaluate",
        "params": {"expression": expression, "returnByValue": True,
                   "awaitPromise": await_promise, "userGesture": True},
    }))
    deadline = time.time() + timeout
    while time.time() < deadline:
        message = json.loads(ws.recv_text())
        if message.get("id") != ws._id:
            continue
        if "error" in message:
            raise CdpError(json.dumps(message["error"], ensure_ascii=False))
        result = message.get("result", {})
        if "exceptionDetails" in result:
            raise CdpError("JS exception: " + json.dumps(result["exceptionDetails"], ensure_ascii=False)[:400])
        return result.get("result", {})
    raise CdpError("evaluate timed out")


_READ_JS = r"""
(() => {
  const pick = document.querySelector('main') || document.querySelector('[role="main"]') || document.body;
  const copy = pick.cloneNode(true);
  copy.querySelectorAll('textarea, input, [contenteditable="true"], [data-aep-composer="1"]').forEach(el => el.remove());
  return (copy.innerText || copy.textContent || '').slice(0, 200000);
})()
"""

_COMPOSER_JS = r"""
(() => {
  const nodes = Array.from(document.querySelectorAll('textarea, [contenteditable="true"], input[type="text"]'));
  const visible = nodes.filter(el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 40 && r.height > 12 && s.visibility !== 'hidden' && s.display !== 'none';
  });
  if (!visible.length) return null;
  visible.sort((a, b) => b.getBoundingClientRect().bottom - a.getBoundingClientRect().bottom);
  const el = visible[0];
  el.setAttribute('data-aep-composer', '1');
  const r = el.getBoundingClientRect();
  return {tag: el.tagName, editable: el.isContentEditable, rect: {w: Math.round(r.width), h: Math.round(r.height), bottom: Math.round(r.bottom)}};
})()
"""

_WRITE_JS = r"""
((text) => {
  const el = document.querySelector('[data-aep-composer="1"]');
  if (!el) return {ok: false, reason: 'NO_COMPOSER'};
  el.focus();
  if (el.isContentEditable) {
    el.innerText = text;
  } else {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, text);
  }
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return {ok: true, length: (el.value !== undefined ? el.value : el.innerText || '').length};
})(%s)
"""

_SUBMIT_JS = r"""
(() => {
  const el = document.querySelector('[data-aep-composer="1"]');
  if (!el) return {submitted: false, reason: 'NO_COMPOSER'};
  const form = el.closest('form');
  if (!form) return {submitted: false, reason: 'NO_FORM_BOUND_TO_COMPOSER'};
  const buttons = Array.from(form.querySelectorAll('button[type="submit"], input[type="submit"]'));
  const visible = buttons.filter(button => {
    const rect = button.getBoundingClientRect();
    const style = getComputedStyle(button);
    return !button.disabled && rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  });
  if (visible.length !== 1) return {submitted: false, reason: 'SUBMIT_CONTROL_NOT_UNIQUE', count: visible.length};
  visible[0].click();
  return {submitted: true, method: 'unique-form-submit-button'};
})()
"""


def read_transcript(ws: _WebSocket) -> str:
    value = evaluate(ws, _READ_JS).get("value")
    return value if isinstance(value, str) else ""


def locate_composer(ws: _WebSocket) -> dict | None:
    return evaluate(ws, _COMPOSER_JS).get("value")


def type_message(ws: _WebSocket, text: str) -> dict:
    return evaluate(ws, _WRITE_JS % json.dumps(text)).get("value") or {"ok": False}


def submit(ws: _WebSocket) -> dict:
    return evaluate(ws, _SUBMIT_JS).get("value") or {"submitted": False}


def contains(ws: _WebSocket, needle: str) -> bool:
    body = read_transcript(ws)
    return bool(needle) and needle in body


def deliver(ws_url: str, text: str, *, event_id: str = "", wait_seconds: float = 8.0) -> dict:
    """Type, submit and read back. Success is 'visible in the same DOM', never 'accepted'."""
    ws = connect(ws_url)
    try:
        composer = locate_composer(ws)
        if not composer:
            return {"status": "NO_COMPOSER", "task_delivered": False, "agent_accepted": False,
                    "note": "CDP found no editable composer; fall back to another transport."}
        written = type_message(ws, text)
        if not written.get("ok"):
            return {"status": "WRITE_FAILED", "composer": composer, "write": written,
                    "task_delivered": False, "agent_accepted": False}
        submitted = submit(ws)
        if not submitted.get("submitted"):
            return {"status": "SUBMIT_UNVERIFIED", "composer": composer, "write": written,
                    "submit": submitted, "task_delivered": False, "agent_accepted": False}
        needle = event_id or (text[:24] if text else "")
        visible = False
        polls = 0
        deadline = time.time() + max(1.0, wait_seconds)
        body = ""
        while time.time() < deadline:
            time.sleep(1.0)
            polls += 1
            body = read_transcript(ws)
            if needle and needle in body:
                visible = True
                break
        return {
            "status": "message-visible" if visible else "submitted-unconfirmed",
            "composer": composer, "write": written, "submit": submitted,
            "readback_polls": polls, "task_delivered": visible, "agent_accepted": False,
            "transcript_chars": len(body),
            "note": "task_delivered means the event id was read back from the same DOM; only the agent's own reply is an ACK.",
        }
    finally:
        ws.close()
