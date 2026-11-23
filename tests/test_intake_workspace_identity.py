"""An explicit workspace must never be silently replaced during intake."""
import pytest

from agent_evolution_protocol.federation import intake


def test_missing_explicit_workspace_does_not_create_or_dispatch(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(intake.engine, "create", lambda *a, **kw: calls.append("created"))
    with pytest.raises(ValueError, match="workspace state not found"):
        intake.intake(tmp_path / "state", tmp_path / "coord", text="task", agents=["alpha"],
                      workspace_id="W-" + "a" * 16)
    assert calls == []
    assert not (tmp_path / "coord").exists()


def test_explicit_workspace_read_failure_never_creates_replacement(tmp_path, monkeypatch):
    calls = []
    def fail(*args):
        raise OSError("state unavailable")
    monkeypatch.setattr(intake.engine, "load", fail)
    monkeypatch.setattr(intake.engine, "create", lambda *a, **kw: calls.append("created"))
    with pytest.raises(OSError, match="state unavailable"):
        intake.intake(tmp_path / "state", tmp_path / "coord", text="task", agents=["alpha"],
                      workspace_id="W-" + "a" * 16)
    assert calls == []
