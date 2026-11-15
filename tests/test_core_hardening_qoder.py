"""Regression tests for the 2026-11-13 audit hardening pass.

Each test pins one failure mode that could not be caught by a regex unit test
alone: a lock contention that used to fall through to unlocked adapter input, an
event id that could escape the receipts directory, two producer id vocabularies
the dispatch gate refused, credentials persisted into command memory, and a
stored fingerprint whose digest was trusted without being recomputed.
"""

import json
from pathlib import Path

import pytest

from agent_evolution_protocol.coordination import (
    battle_orders,
    command_memory,
    commander,
    dispatch_gate,
    session_fingerprint,
    wake_daemon,
)


WINDOW = {"pid": 4242, "title": "Qoder", "exe": r"D:\Programs\Qoder\Qoder.exe"}
PROFILE = {"exe": r"D:\Programs\Qoder\Qoder.exe",
           "session_marker": "赛事夺冠执行", "model_marker": "DeepSeek-Flash"}
WAKE_ID = "WAKE-20261029T000000000000Z-a1b2c3d4"
PULSE_ID = "PULSE-0123456789ABCDEF0123"


def _wake_root(tmp_path: Path) -> tuple[Path, Path]:
    queue = tmp_path / "wake_queue"
    queue.mkdir()
    adapters = tmp_path / "wake_adapters"
    adapters.mkdir()
    (adapters / "qoder.ps1").write_text("", encoding="utf-8")
    return queue, adapters


def _point_daemon(monkeypatch, tmp_path: Path, queue: Path, adapters: Path) -> None:
    monkeypatch.setattr(wake_daemon, "QUEUE", queue)
    monkeypatch.setattr(wake_daemon, "DONE", tmp_path / "wake_done")
    monkeypatch.setattr(wake_daemon, "FAILED", tmp_path / "wake_failed")
    monkeypatch.setattr(wake_daemon, "PENDING", tmp_path / "wake_pending")
    monkeypatch.setattr(wake_daemon, "LOG", tmp_path / "wake_log.jsonl")
    monkeypatch.setattr(wake_daemon, "ADAPTERS", adapters)


def _wake_event(queue: Path, name: str) -> Path:
    path = queue / name
    path.write_text(json.dumps({
        "id": path.stem, "type": "wake_request", "sender": "codex", "to": "qoder",
        "task": "T1", "reason": "check status",
    }), encoding="utf-8")
    return path


def test_wake_consume_defers_on_lock_contention_without_running_the_adapter(tmp_path, monkeypatch):
    queue, adapters = _wake_root(tmp_path)
    _point_daemon(monkeypatch, tmp_path, queue, adapters)
    wake = _wake_event(queue, "second.json")
    calls = []
    monkeypatch.setattr(wake_daemon.subprocess, "run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(wake_daemon.shutil, "which", lambda name: "pwsh.exe")

    with battle_orders._task_writer_lock(tmp_path, "wake-second"):
        assert wake_daemon.consume(wake) == 0

    assert calls == []
    assert wake.is_file()
    entries = [json.loads(line) for line in (tmp_path / "wake_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert entries[-1]["status"] == "deferred-busy"
    assert entries[-1]["id"] == "second"


def test_invalid_wake_filename_is_quarantined_without_running_the_adapter(tmp_path, monkeypatch):
    queue, adapters = _wake_root(tmp_path)
    _point_daemon(monkeypatch, tmp_path, queue, adapters)
    wake = _wake_event(queue, "bad name.json")
    calls = []
    monkeypatch.setattr(wake_daemon.subprocess, "run", lambda *a, **k: calls.append((a, k)))

    assert wake_daemon.consume(wake) == 1

    assert calls == []
    assert not wake.exists()
    assert (tmp_path / "wake_failed" / "bad name.json").is_file()
    entries = [json.loads(line) for line in (tmp_path / "wake_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert entries[-1]["status"] == "invalid-event-id"


@pytest.mark.parametrize("event_id", [
    "..\\..\\pwn",
    "EVT-1/../../pwn",
    "EVT-1\nX",
    "..",
    "-EVT-1",
    "AEP:ACK",
])
def test_submit_order_rejects_path_and_control_characters_in_event_id(tmp_path, event_id):
    with pytest.raises(ValueError, match="invalid event id"):
        commander.submit_order(tmp_path, target="qoder", sender="codex", task="T1",
                               text="hello", event_id=event_id)
    assert not list((tmp_path / "commander" / "inbox").glob("*.json"))


@pytest.mark.parametrize("event_id", [
    "EVT-12345678", "20261102T073800053004Z-4971a758", WAKE_ID, "AEP-OLDER-1", "e1",
])
def test_submit_order_accepts_identifier_shaped_event_ids(tmp_path, event_id):
    order = commander.submit_order(tmp_path, target="qoder", sender="codex", task="T1",
                                   text="hello", event_id=event_id)
    assert order["event_id"] == event_id
    assert event_id in order["text"]


@pytest.mark.parametrize("event_id", [WAKE_ID, PULSE_ID])
def test_wake_and_pulse_orders_reach_the_dispatch_gate(tmp_path, event_id):
    order = commander.submit_order(tmp_path, target="qoder", sender="codex", task="GENERAL",
                                   text="check status", event_id=event_id)
    verdict = dispatch_gate.check(order, profile={"agent": "qoder"})
    assert verdict["verdict"] == "send", verdict["detail"]


def test_a_second_wake_id_in_one_body_is_still_refused(tmp_path):
    order = commander.submit_order(tmp_path, target="qoder", sender="codex", task="GENERAL",
                                   text="check status", event_id=WAKE_ID)
    order["text"] += "\nstale AEP-ACK:WAKE-20261029T000000000001Z-a1b2c3d5"
    order["content_sha256"] = dispatch_gate.content_sha256(order["text"])
    verdict = dispatch_gate.check(order, profile={"agent": "qoder"})
    assert verdict["verdict"] == "refuse"
    assert verdict["reason"] == "one_event_id"


def test_command_memory_redacts_credentials_before_persisting(tmp_path):
    secrets = [
        "bearer-token-1234567890",
        "hunter2",
        "sk-abcdefgh12345678",
        "AKIAABCDEFGHIJKLMNOP",
        "ghp_" + "A" * 30,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
        "session-cookie-value",
        "ghb-token-value",
    ]
    command = (
        'curl -H "Authorization: Bearer bearer-token-1234567890" '
        '--data "password=hunter2&api_key=sk-abcdefgh12345678" '
        '--token ghb-token-value'
    )
    output = (
        "AWS_ACCESS_KEY_ID=AKIAABCDEFGHIJKLMNOP\n"
        "X-Auth: ghp_" + "A" * 30 + "\n"
        "Cookie: session=session-cookie-value\n"
        "JWT eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop\n"
        "WinError 206 Filename too long"
    )
    row = command_memory.record(tmp_path, agent="qoder", command=command, output=output,
                                exit_code=1, source="audit-test")
    stored = (tmp_path / "command_memory" / "observations" / f"{row['id']}.json").read_text(encoding="utf-8")
    for secret in secrets:
        assert secret not in stored, secret
    assert "<redacted>" in stored
    assert row["category"] == "long_path"


def test_tampered_stored_fingerprint_is_detected(tmp_path):
    observed = session_fingerprint.observe(agent="qoder", window=WINDOW, profile=PROFILE)
    session_fingerprint.record(tmp_path, "qoder", observed)
    target = session_fingerprint.path(tmp_path, "qoder")
    row = json.loads(target.read_text(encoding="utf-8"))
    assert row["fingerprint"] == session_fingerprint.compute(row)

    row["session_marker"] = "另一个会话"
    target.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")

    result = session_fingerprint.compare(session_fingerprint.read(tmp_path, "qoder"), observed)
    assert result["match"] is False
    assert "inconsistent" in result["reason"]
    assert not list(target.parent.glob("*.tmp"))


def test_fingerprint_record_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    observed = session_fingerprint.observe(agent="qoder", window=WINDOW, profile=PROFILE)
    session_fingerprint.record(tmp_path, "qoder", observed)
    target = session_fingerprint.path(tmp_path, "qoder")
    assert json.loads(target.read_text(encoding="utf-8"))["agent"] == "qoder"
    assert not list(target.parent.glob("*.tmp"))


def test_wake_pending_write_leaves_valid_json_and_no_fixed_temp_file(tmp_path):
    target = tmp_path / "wake_pending" / "event.json"
    wake_daemon.write_json(target, {"id": "event", "adapter_status": "observed"})
    assert json.loads(target.read_text(encoding="utf-8"))["adapter_status"] == "observed"
    assert not list(target.parent.glob("*.tmp"))
