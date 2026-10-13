"""Low-frequency patrol that keeps every agent's transcript on the shared bus.

A bus nobody writes to is decoration. This patrol walks the registered clients,
captures whatever their window exposes, cleans it and publishes the delta. It is
strictly read-only: it never types, never sends, never launches and never wakes
a model. Capture does bring each window to the foreground briefly, so run it
when nobody is typing, and keep the interval low.

Usage:
    python -m agent_evolution_protocol.coordination.transcript_patrol \
        --root <coord_root> run
    python -m agent_evolution_protocol.coordination.transcript_patrol \
        --root <coord_root> status
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from . import transcript_bus

BASE = Path(__file__).resolve().parent
UIA = BASE / "wake_adapters" / "uia_session.ps1"
CONFIG_NAME = "transcript_patrol.json"
STATE_NAME = "patrol_state.json"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def config_path(coord_root: Path) -> Path:
    return Path(coord_root) / "transcript_patrol" / CONFIG_NAME


def state_path(coord_root: Path) -> Path:
    return Path(coord_root) / "transcript_patrol" / STATE_NAME


def default_config(coord_root: Path) -> dict:
    """Seed a config from whatever clients already have a UI profile."""
    clients: list[dict[str, str]] = []
    profiles = Path(coord_root) / "ui_profiles"
    if profiles.is_dir():
        for path in sorted(profiles.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(data.get("agent", "")).strip():
                clients.append({
                    "agent": data["agent"],
                    "process": data.get("process", ""),
                    "title": data.get("title", ""),
                    "transport": data.get("transport", "uia"),
                })
    return {"clients": clients, "note": "Read-only capture. Never types or wakes."}


def load_config(coord_root: Path) -> dict:
    path = config_path(coord_root)
    if not path.is_file():
        raise FileNotFoundError(f"patrol config missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    clients = data.get("clients")
    if not isinstance(clients, list) or not clients:
        raise ValueError("patrol config needs a non-empty 'clients' list")
    for item in clients:
        if not str(item.get("agent", "")).strip():
            raise ValueError("each client needs an 'agent' name")
        if not str(item.get("process", "")).strip():
            raise ValueError(f"client {item.get('agent')} needs a 'process' name")
    return data


def capture(client: dict[str, Any], out_file: Path, timeout: int = 90) -> dict:
    """Ask the transport for a transcript dump. Read-only."""
    pwsh = shutil.which("pwsh.exe")
    if not pwsh:
        return {"ok": False, "reason": "pwsh_missing"}
    args = [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(UIA),
            "-Agent", client["agent"], "-ProcessName", client["process"],
            "-Mode", "capture", "-OutFile", str(out_file)]
    if str(client.get("title", "")).strip():
        args += ["-ExpectedTitle", client["title"]]
    if str(client.get("exe", "")).strip():
        args += ["-ExpectedPath", client["exe"]]
    if client.get("allow_unknown_path"):
        args.append("-AllowUnknownPath")
    if client.get("ocr_verify"):
        args.append("-OcrVerify")
    try:
        done = subprocess.run(args, text=True, capture_output=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "capture_timeout"}
    text = out_file.read_text(encoding="utf-8", errors="replace") if out_file.is_file() else ""
    meta: dict[str, Any] = {}
    for line in reversed((done.stdout or "").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            meta = value
            break
    if not text.strip():
        return {"ok": False, "reason": meta.get("status") or "empty_capture",
                "returncode": done.returncode,
                "stderr_tail": (done.stderr or "")[-400:]}
    return {"ok": True, "chars": len(text), "status": meta.get("status", ""),
            "reported_lines": meta.get("transcript_lines", 0)}


def run(coord_root: Path, *, only: str = "") -> dict:
    coord_root = Path(coord_root)
    config = load_config(coord_root)
    results = []
    with tempfile.TemporaryDirectory(prefix="aep-patrol-") as folder:
        for client in config["clients"]:
            agent = client["agent"]
            if only and agent != only:
                continue
            out = Path(folder) / f"{agent}.txt"
            got = capture(client, out)
            if not got["ok"]:
                results.append({"agent": agent, "captured": False,
                                "reason": got["reason"]})
                continue
            try:
                published = transcript_bus.publish(
                    coord_root, agent=agent, text=out.read_text(encoding="utf-8", errors="replace"),
                    source=str(client.get("transport", "uia")),
                    session_id=str(client.get("session_marker", "")),
                    window_title=str(client.get("title", "")),
                )
            except ValueError as exc:
                results.append({"agent": agent, "captured": True, "published": False,
                                "reason": str(exc)})
                continue
            results.append({"agent": agent, "captured": True, "published": True,
                            "added_lines": published["added_line_count"],
                            "observed_lines": published["observed_line_count"],
                            "changed": published["changed"]})

    payload = {
        "ran_at": _utc(),
        "clients": results,
        "captured": sum(1 for r in results if r.get("captured")),
        "blind": [r["agent"] for r in results if not r.get("captured")],
        "note": "A blind client means the transport could not read it. That is a coverage gap to fix, never proof that the agent is idle.",
    }
    path = state_path(coord_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[2] / "coordination")
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="capture every registered client and publish the delta")
    r.add_argument("--agent", default="", help="only this client")
    r.set_defaults(func=lambda a: print(json.dumps(run(a.root, only=a.agent),
                                                   ensure_ascii=False, indent=2)) or 0)

    i = sub.add_parser("init", help="seed a config from existing UI profiles")
    i.set_defaults(func=lambda a: _init(a.root))

    s = sub.add_parser("status", help="last patrol result")
    s.set_defaults(func=lambda a: _status(a.root))

    args = parser.parse_args()
    return int(args.func(args))


def _init(root: Path) -> int:
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        print(json.dumps({"config": str(path), "created": False}, ensure_ascii=False))
        return 0
    path.write_text(json.dumps(default_config(root), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(json.dumps({"config": str(path), "created": True,
                      "clients": len(default_config(root)["clients"])}, ensure_ascii=False))
    return 0


def _status(root: Path) -> int:
    path = state_path(root)
    if not path.is_file():
        print(json.dumps({"ran": False}, ensure_ascii=False))
        return 0
    print(path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
