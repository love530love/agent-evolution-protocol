from pathlib import Path
import sys

import pytest

from agent_evolution_protocol.coordination import desktop_runner


def _discovery(windows, *, anchored=False):
    return {"clients": [{"agent": "qoder_cn", "windows": windows,
                          "anchored": anchored, "unique": len(windows) == 1}]}


def test_wait_for_ready_returns_unique_session(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop_runner, "_process_facts", lambda pid, exe="": {
        "pid": pid, "exe": exe or "C:/Qoder.exe", "process_start_time": "2026-01-01T00:00:00+00:00",
        "user": "user", "windows_session_id": 2,
    })
    result = desktop_runner.wait_for_ready(
        tmp_path, "qoder_cn", timeout=0,
        discover=lambda _root, _agent: _discovery([{"pid": 123, "hwnd": 456, "title": "Qoder CN"}]),
    )
    assert result["status"] == "READY"
    assert result["session"]["pid"] == 123
    assert result["session"]["windows_session_id"] == 2
    assert result["execution_started"] is False


def test_wait_for_ready_blocks_ambiguous_windows(tmp_path):
    result = desktop_runner.wait_for_ready(
        tmp_path, "qoder_cn", timeout=0,
        discover=lambda _root, _agent: _discovery([
            {"pid": 123, "hwnd": 456, "title": "Qoder CN"},
            {"pid": 124, "hwnd": 457, "title": "Qoder CN"},
        ]),
    )
    assert result["status"] == "AMBIGUOUS_SESSION"
    assert result["execution_started"] is False


def test_launch_defaults_to_preview(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop_runner.client_registry, "profile", lambda _root, _agent: {
        "agent": "qoder_cn", "exe": "C:/Qoder/Qoder.exe", "launch_args": ["--profile", "main"]
    })
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("preview must not launch a process")
    monkeypatch.setattr(desktop_runner.client_registry, "launch", fail_if_called)
    result = desktop_runner.launch(tmp_path, "qoder_cn")
    assert result["status"] == "PREVIEW"
    assert result["execution_started"] is False


@pytest.mark.skipif(sys.platform != "win32",
                    reason="enumerating desktop windows goes through ctypes.windll")
def test_ensure_session_preview_does_not_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop_runner.client_registry, "profile", lambda _root, _agent: {
        "agent": "qoder_cn", "exe": "C:/Qoder/Qoder.exe", "launch_args": []
    })
    result = desktop_runner.ensure_session(tmp_path, "qoder_cn", apply=False, timeout=0)
    assert result["status"] == "DESKTOP_SESSION_UNAVAILABLE"
    assert result["execution_started"] is False


def test_wait_for_ready_uses_readiness_not_fixed_sleep(tmp_path):
    calls = {"count": 0}

    def discover(_root, _agent):
        calls["count"] += 1
        return _discovery([]) if calls["count"] < 2 else _discovery([{"pid": 222, "title": "Agent"}])

    result = desktop_runner.wait_for_ready(tmp_path, "qoder_cn", timeout=0.1,
                                           poll_interval=0.001, discover=discover)
    assert result["status"] == "READY"
    assert calls["count"] >= 2
