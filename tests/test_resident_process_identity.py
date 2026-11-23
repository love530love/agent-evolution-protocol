import os

import pytest

from agent_evolution_protocol.coordination import process_identity


def test_current_process_can_be_probed_without_being_signalled():
    assert process_identity.alive(os.getpid()) is True
    started = process_identity.current_started_at()
    if started:
        assert process_identity.verify(os.getpid(), started) is True
        assert process_identity.verify_precise(os.getpid(), started) is True


def test_precise_comparison_detects_fast_reuse_and_rejects_coarse_claims(monkeypatch):
    from datetime import datetime, timezone
    started = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(process_identity, "started_at", lambda pid: started)
    assert process_identity.verify_precise(42, "2026-01-01T00:00:01.000000+00:00") is True
    assert process_identity.verify_precise(42, "2026-01-01T00:00:00.000000+00:00") is False
    assert process_identity.verify_precise(42, "2026-01-01T00:00:01+00:00") is None
    assert process_identity.verify_precise(42, "2026-01-01T00:00:01.000000") is None


@pytest.mark.parametrize("pid", [None, True, -1, 0, 1 << 80, "123"])
def test_invalid_process_ids_are_unknown(pid):
    assert process_identity.alive(pid) is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal-zero permission simulation")
def test_inaccessible_process_is_unknown_not_dead(monkeypatch):
    def denied(pid, signal):
        raise PermissionError("test")
    monkeypatch.setattr(process_identity.os, "kill", denied)
    assert process_identity.alive(1234) is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal-zero missing simulation")
def test_missing_process_is_dead(monkeypatch):
    def missing(pid, signal):
        raise ProcessLookupError("test")
    monkeypatch.setattr(process_identity.os, "kill", missing)
    assert process_identity.alive(1234) is False


@pytest.mark.parametrize("error,expected", [(5, None), (87, False), (6, None)])
def test_windows_access_denied_is_unknown_not_dead(monkeypatch, error, expected):
    import ctypes
    from types import SimpleNamespace

    class OpenProcess:
        def __call__(self, *args):
            return 0
    kernel = SimpleNamespace(OpenProcess=OpenProcess())
    monkeypatch.setattr(process_identity, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    assert process_identity.alive(1234) is expected
