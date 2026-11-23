"""Real process and restart tests for the experimental router event fence."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from agent_evolution_protocol.coordination import session_registry, wake_event_fence, wake_router


def setup_session(root):
    session_registry.register(root, agent="qoder", workspace=str(root), provider="uia", session_id="session-a")


def receipt(event="evt"):
    return {"event_id_visible": True, "visible_event_id": event, "visible_session_id": "session-a"}


def test_repeated_event_does_not_repeat_send_and_rejects_changed_body(tmp_path):
    setup_session(tmp_path)
    sent = []
    transport = wake_router.Transport("uia", lambda _: sent.append(1) or receipt())
    first = wake_router.route(tmp_path, agent="qoder", text="private text", event_id="evt", transports=[transport], execute=True)
    second = wake_router.route(tmp_path, agent="qoder", text="private text", event_id="evt", transports=[transport], execute=True)
    conflict = wake_router.route(tmp_path, agent="qoder", text="changed", event_id="evt", transports=[transport], execute=True)
    assert first["status"] == second["status"] == "DELIVERED"
    assert second["already_recorded"] is True and second["attempts"] == []
    assert conflict["status"] == "EVENT_ID_CONFLICT"
    assert sent == [1]
    assert "private text" not in wake_event_fence.event_paths(tmp_path, "evt")[1].read_text(encoding="utf-8")


def test_parallel_threads_send_event_only_once(tmp_path):
    setup_session(tmp_path)
    sent = []
    def send(_):
        sent.append(1)
        time.sleep(.05)
        return receipt()
    def call(_):
        return wake_router.route(tmp_path, agent="qoder", text="x", event_id="evt",
                                 transports=[wake_router.Transport("uia", send)], execute=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(call, range(8)))
    assert len(sent) == 1
    assert sum(not row.get("already_recorded", False) for row in results) == 1
    assert all(row["status"] == "DELIVERED" for row in results)


def test_process_death_after_possible_send_blocks_replay(tmp_path):
    setup_session(tmp_path)
    script = '''
import os, sys
from pathlib import Path
from agent_evolution_protocol.coordination import wake_router
root = Path(sys.argv[1])
def send(_):
    (root / "sent.txt").write_text("sent", encoding="utf-8")
    os._exit(37)
wake_router.route(root, agent="qoder", text="x", event_id="evt", transports=[wake_router.Transport("uia", send)], execute=True)
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"), PYTHONUTF8="1")
    child = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=env, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert child.returncode == 37 and (tmp_path / "sent.txt").read_text(encoding="utf-8") == "sent"
    sent = []
    retry = wake_router.route(tmp_path, agent="qoder", text="x", event_id="evt",
                              transports=[wake_router.Transport("uia", lambda _: sent.append(1))], execute=True)
    assert retry["status"] == "UNCERTAIN" and retry["already_recorded"] is True
    assert not sent


def test_two_processes_cannot_send_the_same_event(tmp_path):
    setup_session(tmp_path)
    script = '''
import sys, time
from pathlib import Path
from agent_evolution_protocol.coordination import wake_router
root = Path(sys.argv[1])
def send(_):
    with (root / "calls.txt").open("a", encoding="utf-8") as stream:
        stream.write("sent\\n")
    time.sleep(.25)
    return {"event_id_visible": True, "visible_event_id": "evt", "visible_session_id": "session-a"}
r = wake_router.route(root, agent="qoder", text="x", event_id="evt", transports=[wake_router.Transport("uia", send)], execute=True)
assert r["status"] == "DELIVERED"
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"), PYTHONUTF8="1")
    children = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path)], env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) for _ in range(2)]
    for child in children:
        out, err = child.communicate(timeout=30)
        assert child.returncode == 0, (out, err)
    assert (tmp_path / "calls.txt").read_text(encoding="utf-8").splitlines() == ["sent"]


def test_corrupt_record_and_final_write_failure_never_trigger_retry(tmp_path, monkeypatch):
    setup_session(tmp_path)
    real_write = wake_event_fence.write
    def fail_final(path, **kwargs):
        if kwargs["state"] == "FINAL":
            raise OSError("final ledger write failed")
        return real_write(path, **kwargs)
    monkeypatch.setattr(wake_event_fence, "write", fail_final)
    with pytest.raises(OSError, match="final ledger"):
        wake_router.route(tmp_path, agent="qoder", text="x", event_id="evt",
                          transports=[wake_router.Transport("uia", lambda _: receipt())], execute=True)
    calls = []
    retry = wake_router.route(tmp_path, agent="qoder", text="x", event_id="evt",
                              transports=[wake_router.Transport("uia", lambda _: calls.append(1))], execute=True)
    assert retry["status"] == "UNCERTAIN" and not calls
    wake_event_fence.event_paths(tmp_path, "evt")[1].write_text("{", encoding="utf-8")
    retry = wake_router.route(tmp_path, agent="qoder", text="x", event_id="evt",
                              transports=[wake_router.Transport("uia", lambda _: calls.append(1))], execute=True)
    assert retry["reason"] == "EVENT_FENCE_UNREADABLE" and not calls


def test_invalid_request_does_not_call_transport(tmp_path):
    setup_session(tmp_path)
    calls = []
    with pytest.raises(ValueError, match="event_id"):
        wake_router.route(tmp_path, agent="qoder", text="x", event_id="", transports=[], execute=True)
    with pytest.raises(ValueError, match="text"):
        wake_router.route(tmp_path, agent="qoder", text=None, event_id="evt",
                          transports=[wake_router.Transport("uia", lambda _: calls.append(1))], execute=True)
    assert not calls


def test_recorded_event_is_uncertain_if_session_disappears(tmp_path):
    setup_session(tmp_path)
    wake_router.route(tmp_path, agent="qoder", text="x", event_id="evt",
                      transports=[wake_router.Transport("uia", lambda _: receipt())], execute=True)
    session_registry.transition(tmp_path, agent="qoder", session_id="session-a", state="offline")
    result = wake_router.route(tmp_path, agent="qoder", text="x", event_id="evt", transports=[], execute=True)
    assert result["status"] == "UNCERTAIN"
    assert result["reason"] == "EVENT_RECORDED_SESSION_UNAVAILABLE"


def test_event_cannot_be_reused_for_changed_conversation(tmp_path):
    setup_session(tmp_path)
    calls = []
    transport = wake_router.Transport("uia", lambda _: calls.append(1) or receipt())
    wake_router.route(tmp_path, agent="qoder", text="x", event_id="evt", transports=[transport], execute=True)
    session_registry.register(tmp_path, agent="qoder", workspace=str(tmp_path), provider="uia",
                              session_id="session-a", conversation_id="another-conversation")
    result = wake_router.route(tmp_path, agent="qoder", text="x", event_id="evt", transports=[transport], execute=True)
    assert result["status"] == "EVENT_ID_CONFLICT"
    assert calls == [1]


def test_lock_timeout_is_uncertain(tmp_path, monkeypatch):
    from agent_evolution_protocol.federation.locking import LockTimeout
    def busy(*args):
        raise LockTimeout("busy")
    monkeypatch.setattr(wake_event_fence, "acquire", busy)
    result = wake_router.route(tmp_path, agent="qoder", text="x", event_id="evt", transports=[], execute=True)
    assert result["status"] == "UNCERTAIN"
    assert result["reason"] == "EVENT_OR_SESSION_LOCK_BUSY"


def test_event_file_access_denied_is_not_treated_as_absent(tmp_path, monkeypatch):
    setup_session(tmp_path)
    ledger = wake_event_fence.event_paths(tmp_path, "evt")[1]
    real_read = Path.read_text
    def denied(path, *args, **kwargs):
        if path == ledger:
            raise PermissionError("denied")
        return real_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", denied)
    calls = []
    result = wake_router.route(tmp_path, agent="qoder", text="x", event_id="evt",
                               transports=[wake_router.Transport("uia", lambda _: calls.append(1))], execute=True)
    assert result["status"] == "UNCERTAIN"
    assert result["reason"] == "EVENT_FENCE_UNREADABLE" and not calls
