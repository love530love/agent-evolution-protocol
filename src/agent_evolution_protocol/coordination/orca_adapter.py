"""Opt-in local Orca CLI adapter. Never launches apps, creates sessions or infers ACK."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from .battle_orders import _atomic_json, _task_writer_lock

UPSTREAM = "ad4952e1dabcfff0871fa7a227cb00f0ac5d269b"
NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
IDENTITY = ("handle", "executionHostId", "ptyId", "incarnationId", "worktreeId", "worktreePath", "agentIdentity")


def _name(name):
    if not isinstance(name, str) or not NAME.fullmatch(name) or name in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
        raise ValueError("invalid agent name")
    return name


def configure(root, command):
    path = Path(command).resolve()
    if not Path(command).is_absolute() or not path.is_file() or path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        raise ValueError("use an existing absolute CLI executable, not a shell script")
    destination = Path(root) / "orca" / "config.json"
    with _task_writer_lock(Path(root), "orca-adapter"):
        if destination.exists():
            raise ValueError("Orca already configured; inspect configuration before replacing it")
        _atomic_json(destination, {"command": str(path), "upstream_contract": UPSTREAM})
    return {"configured": True, "execution_started": False}


def _call(root, arguments, timeout=15):
    config = json.loads((Path(root) / "orca" / "config.json").read_text(encoding="utf-8"))
    command = config["command"]
    if not Path(command).is_absolute() or not Path(command).is_file() or Path(command).suffix.lower() in {".cmd", ".bat", ".ps1"}:
        raise ValueError("configured CLI executable is unavailable or invalid")
    run = subprocess.run([command, *arguments, "--host", "local", "--json"],
        cwd=str(Path(root).resolve()), capture_output=True, text=True, encoding="utf-8", errors="strict",
        timeout=timeout, check=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if run.returncode:
        raise ValueError(f"Orca CLI failed (exit {run.returncode}); no automatic retry")
    response = json.loads(run.stdout)
    if not isinstance(response, dict) or response.get("ok") is not True or not isinstance(response.get("result"), dict):
        raise ValueError("unsupported Orca JSON envelope")
    metadata = response.get("_meta")
    if not isinstance(metadata, dict):
        raise ValueError("unsupported Orca runtime metadata")
    runtime = metadata.get("runtimeId")
    if not isinstance(runtime, str) or not runtime:
        raise ValueError("unverifiable Orca runtime identity")
    return runtime, response["result"]


def probe(root):
    runtime, status = _call(root, ["status"])
    health = status.get("runtime")
    if not isinstance(health, dict) or health.get("reachable") is not True or health.get("runtimeId") != runtime:
        raise ValueError("Orca runtime health or identity is unverifiable")
    listed_runtime, terminals = _call(root, ["terminal", "list"])
    if runtime != listed_runtime:
        raise ValueError("runtime changed during discovery")
    return {"runtime_id": runtime, "runtime": status.get("runtime", {}), "inventory": terminals,
            "agent_ack_verified": False, "desktop_input_performed": False}


def _terminal(discovery, handle):
    inventory = discovery["inventory"]
    if inventory.get("truncated") is not False or not isinstance(inventory.get("terminals"), list):
        raise ValueError("terminal inventory is incomplete or unsupported")
    matches = [row for row in inventory["terminals"] if isinstance(row, dict) and row.get("handle") == handle]
    if len(matches) != 1:
        raise ValueError("terminal is missing or not unique; do not guess a replacement")
    terminal = matches[0]
    if any(not isinstance(terminal.get(key), str) or not terminal[key] for key in IDENTITY):
        raise ValueError("terminal identity fields are unverifiable")
    if terminal["agentIdentity"] not in {"codex", "claude"}:
        raise ValueError("initial adapter supports only observed codex/claude agent terminals, not shells")
    scope = inventory.get("hostScope")
    if not isinstance(scope, dict) or not isinstance(scope.get("hostIds"), list):
        raise ValueError("execution host scope is unverifiable")
    hosts = scope["hostIds"]
    if terminal["executionHostId"] not in hosts or terminal.get("verdict") != "live" or terminal.get("writable") is not True or terminal.get("orphaned"):
        raise ValueError("execution host/PTY is not proven live and writable")
    return {key: terminal[key] for key in IDENTITY}


def bind(root, *, agent, terminal):
    _name(agent)
    with _task_writer_lock(Path(root), "orca-adapter"):
        path = Path(root) / "orca" / "bindings" / f"{agent}.json"
        if path.exists():
            raise ValueError("agent already bound; no silent rebinding")
        discovery = probe(root)
        identity = _terminal(discovery, terminal)
        binding = {"agent": agent, "runtime_id": discovery["runtime_id"], "identity": identity}
        _atomic_json(path, binding)
        return {**binding, "agent_ack_verified": False}


def _verify(root, agent):
    binding = json.loads((Path(root) / "orca" / "bindings" / f"{_name(agent)}.json").read_text(encoding="utf-8"))
    discovery = probe(root)
    identity = _terminal(discovery, binding["identity"]["handle"])
    if discovery["runtime_id"] != binding["runtime_id"] or identity != binding["identity"]:
        raise ValueError("Orca binding is stale; runtime, host or process identity changed")
    return binding, discovery


def read(root, *, agent, cursor=None):
    binding, _ = _verify(root, agent)
    arguments = ["terminal", "read", "--terminal", binding["identity"]["handle"], "--limit", "200"]
    if cursor is not None:
        if not isinstance(cursor, str) or not cursor.isdigit():
            raise ValueError("cursor must be a decimal string")
        arguments += ["--cursor", cursor]
    runtime, result = _call(root, arguments)
    if runtime != binding["runtime_id"] or not isinstance(result.get("terminal"), dict) or result["terminal"].get("handle") != binding["identity"]["handle"]:
        raise ValueError("terminal identity changed while reading")
    return {"terminal": result["terminal"], "role_attributed": False, "agent_ack_verified": False,
            "note": "Terminal text may contain prompt echo; do not ingest it as an assistant reply."}


def wait(root, *, agent, seconds=15):
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 30:
        raise ValueError("wait must be 1..30 seconds")
    binding, _ = _verify(root, agent)
    runtime, result = _call(root, ["terminal", "wait", "--terminal", binding["identity"]["handle"],
        "--for", "tui-idle", "--timeout-ms", str(seconds * 1000)], timeout=seconds + 5)
    if runtime != binding["runtime_id"]:
        raise ValueError("runtime changed during wait")
    return {"wait": result.get("wait"), "agent_ack_verified": False, "task_completed": False,
            "note": "Terminal idle is not an attributed response or task completion."}


def send(root, *, agent, event_id, text, apply=False):
    _name(agent)
    if not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", event_id):
        raise ValueError("invalid event ID")
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 16000 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
        raise ValueError("message must be nonempty, <=16000 UTF-8 bytes and contain no terminal control codes")
    # Reject shell terminals by requiring observed agent identity during binding.
    digest = hashlib.sha256((agent + "\n" + text).encode()).hexdigest()
    event_key = hashlib.sha256(event_id.encode()).hexdigest()
    destination = Path(root) / "orca" / "receipts" / f"{event_key}.json"
    if not apply:
        return {"status": "PLAN_ONLY", "agent": agent, "event_id": event_id, "message_sha256": digest,
                "execution_started": False, "agent_ack_verified": False}
    with _task_writer_lock(Path(root), "orca-adapter"):
        if destination.exists():
            prior = json.loads(destination.read_text(encoding="utf-8"))
            if prior["message_sha256"] != digest:
                raise ValueError("event ID reused with changed agent or message")
            return {**prior, "replayed_without_sending": True}
        binding, discovery = _verify(root, agent)
        if "terminal.prompt-delivery.v1" not in discovery["runtime"].get("capabilities", []):
            raise ValueError("Orca runtime lacks durable prompt delivery; no input sent")
        row = {"status": "RESERVED", "agent": agent, "event_id": event_id, "message_sha256": digest,
               "binding": binding, "agent_ack_verified": False, "conversation_session_verified": False}
        _atomic_json(destination, row)  # A crash from here on must never cause blind retry.
        body = text + f"\n\n[AEP-ACK:{event_id}]"
        try:
            runtime, result = _call(root, ["terminal", "send", "--terminal", binding["identity"]["handle"],
                                         "--text", body, "--enter", "--wait-submit", "5"], timeout=20)
            sent = result.get("send", {})
            if not isinstance(sent, dict):
                raise ValueError("invalid send receipt")
            prompt = sent.get("prompt", {})
            if not isinstance(prompt, dict):
                raise ValueError("invalid prompt receipt")
            if runtime != binding["runtime_id"] or sent.get("handle") != binding["identity"]["handle"]:
                raise ValueError("identity changed during send")
            verified = (sent.get("accepted") is True and prompt.get("observation") == "supported"
                        and isinstance(prompt.get("requestId"), str) and bool(prompt["requestId"])
                        and "input_accepted" in prompt.get("stages", [])
                        and prompt.get("processIncarnation") not in {None, "", "unknown"})
            row.update(status="PROMPT_ACCEPTED" if verified else "OUTCOME_UNKNOWN", prompt_receipt=prompt)
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            row.update(status="OUTCOME_UNKNOWN", error=type(exc).__name__)
        _atomic_json(destination, row)
        return row
