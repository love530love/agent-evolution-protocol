import ctypes
from ctypes import wintypes
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from agent_evolution_protocol.coordination.external_runner import execute, reconcile_session


CTX = multiprocessing.get_context("spawn")


def _python_plan(source: str, **extra):
    return {"argv": [sys.executable, "-X", "utf8", "-c", source], **extra}


def _run(root: Path, workspace: Path, event="EVT-test-001", text="你好", **kwargs):
    return execute(root, event_id=event, workspace=workspace,
                   plan=_python_plan("import sys; print(sys.stdin.read())"), text=text, **kwargs)


def _input_plan(source: str, session="native-1"):
    return _python_plan(source, input_capable=True, provider="test-provider",
                        target_agent="qoder", session_id=session)


def _child_execute(root: str, workspace: str, event: str, started: str):
    source = (
        "from pathlib import Path; import time; "
        f"Path({started!r}).write_text('started', encoding='utf-8'); time.sleep(2)"
    )
    result = execute(Path(root), event_id=event, workspace=Path(workspace),
                     plan=_input_plan(source), timeout=10)
    Path(root, event + ".child.json").write_text(json.dumps(result), encoding="utf-8")


def test_same_event_runs_once(tmp_path):
    root = tmp_path / "receipts"
    first = _run(root, tmp_path)
    assert _run(root, tmp_path) == first
    assert first["state"] == "PROCESS_EXITED"
    assert first["delivery_verified"] is False


def test_changed_request_refused(tmp_path):
    root = tmp_path / "receipts"
    _run(root, tmp_path)
    with pytest.raises(ValueError, match="different request"):
        _run(root, tmp_path, text="changed")


def test_timeout_never_replayed(tmp_path):
    root = tmp_path / "receipts"
    plan = _python_plan("import time; time.sleep(10)")
    first = execute(root, event_id="timeout", workspace=tmp_path, plan=plan, timeout=.05)
    assert first["state"] == "UNKNOWN"
    assert execute(root, event_id="timeout", workspace=tmp_path, plan=plan, timeout=.05)["state"] == "UNKNOWN"


def test_crash_after_reservation_not_replayed(tmp_path):
    root = tmp_path / "receipts"
    with patch("agent_evolution_protocol.coordination.external_runner.subprocess.Popen", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            _run(root, tmp_path)
    assert _run(root, tmp_path)["state"] == "UNKNOWN"


def test_partial_reservation_is_unknown(tmp_path):
    root = tmp_path / "receipts"
    root.mkdir()
    (root / "EVT-test-001.attempt.json").write_text("{", encoding="utf-8")
    assert _run(root, tmp_path)["state"] == "UNKNOWN"


@pytest.mark.parametrize("argv", ["fake.exe", [], [None], [""]])
def test_invalid_argv_before_reservation(tmp_path, argv):
    with pytest.raises(ValueError):
        execute(tmp_path / "receipts", event_id="test", workspace=tmp_path, plan={"argv": argv})
    assert not (tmp_path / "receipts").exists()


@pytest.mark.parametrize("kwargs", [{"text": {}}, {"timeout": True}, {"timeout": float("nan")}, {"max_output_bytes": True}])
def test_invalid_input_before_reservation(tmp_path, kwargs):
    with pytest.raises(ValueError):
        execute(tmp_path / "receipts", event_id="invalid", workspace=tmp_path,
                plan=_python_plan("print('x')"), **kwargs)
    assert not (tmp_path / "receipts").exists()


def test_real_process_utf8_and_durable_result(tmp_path):
    result = _run(tmp_path / "receipts", tmp_path, event="real", text="真实回读")
    assert result["exit_code"] == 0
    assert "真实回读" in result["stdout"]
    assert json.loads((tmp_path / "receipts/real.result.json").read_text(encoding="utf-8")) == result


def test_output_is_bounded_without_blocking_target(tmp_path):
    source = "import sys; sys.stdout.write('x'*8192); sys.stderr.write('y'*8192)"
    result = execute(tmp_path / "receipts", event_id="bounded", workspace=tmp_path,
                     plan=_python_plan(source), max_output_bytes=128)
    assert result["state"] == "PROCESS_EXITED"
    assert result["stdout_bytes_seen"] == 8192
    assert result["stderr_bytes_seen"] == 8192
    assert len(result["stdout"].encode("utf-8")) == 128
    assert len(result["stderr"].encode("utf-8")) == 128
    assert result["stdout_truncated"] is True
    assert result["stderr_truncated"] is True


def test_same_session_is_exclusive_across_processes(tmp_path):
    root = tmp_path / "receipts"
    marker = tmp_path / "started"
    worker = CTX.Process(target=_child_execute,
                         args=(str(root), str(tmp_path), "one", str(marker)))
    worker.start()
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists(), "first session execution did not start"
        second = execute(root, event_id="two", workspace=tmp_path,
                         plan=_input_plan("print('should not run')"), timeout=.2)
        assert second["state"] == "SESSION_BUSY"
        assert not (root / "two.attempt.json").exists()
    finally:
        worker.join(20)
        if worker.is_alive():
            worker.terminate()
            worker.join(10)


def test_timeout_blocks_new_session_event_until_reconciled(tmp_path):
    root = tmp_path / "receipts"
    first = execute(root, event_id="first", workspace=tmp_path,
                    plan=_input_plan("import time; time.sleep(10)"), timeout=.05)
    assert first["state"] == "UNKNOWN"
    blocked = execute(root, event_id="second", workspace=tmp_path,
                      plan=_input_plan("print('must not run')"), timeout=.2)
    assert blocked["state"] == "SESSION_UNCERTAIN"
    assert not (root / "second.attempt.json").exists()
    recovered = reconcile_session(root, workspace=tmp_path, provider="test-provider",
                                  target_agent="qoder", session_id="native-1",
                                  evidence="observed target conversation; no message was sent")
    assert recovered["state"] == "RECONCILED"
    next_result = execute(root, event_id="third", workspace=tmp_path,
                          plan=_input_plan("print('after reconcile')"))
    assert next_result["state"] == "PROCESS_EXITED"


def test_input_plan_requires_bound_original_session_before_reservation(tmp_path):
    with pytest.raises(ValueError, match="provider, target_agent and existing session_id"):
        execute(tmp_path / "receipts", event_id="input", workspace=tmp_path,
                plan={**_python_plan("print('x')"), "input_capable": True})
    assert not (tmp_path / "receipts").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object containment")
def test_windows_timeout_terminates_exact_descendant_tree(tmp_path):
    marker = tmp_path / "descendant.pid"
    source = (
        "from pathlib import Path; import subprocess, sys, time; "
        "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"Path({str(marker)!r}).write_text(str(child.pid), encoding='utf-8'); time.sleep(60)"
    )
    result = execute(tmp_path / "receipts", event_id="tree", workspace=tmp_path,
                     # Full-suite startup on an occupied Windows desktop can
                     # take longer than the target's first 400ms. The test is
                     # about killing a known *started* descendant, not about
                     # racing bootstrap import time.
                     plan=_python_plan(source), timeout=5)
    assert result["state"] == "UNKNOWN"
    assert result["process_tree_stopped_verified"] is True
    assert marker.exists()
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    handle = api.OpenProcess(0x00100000, False, int(marker.read_text(encoding="utf-8")))
    # A fast Job termination may remove the process before OpenProcess gets a
    # handle. Either "already absent" or "handle observed then signalled" is
    # a verified stop; access-denied is not silently accepted.
    if handle:
        try:
            assert api.WaitForSingleObject(handle, 8000) == 0
        finally:
            api.CloseHandle(handle)
    else:
        assert ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER = PID gone
