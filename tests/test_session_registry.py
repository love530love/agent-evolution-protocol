from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
import pytest
from agent_evolution_protocol.coordination import session_registry as sr


def test_resolution_requires_unique(tmp_path: Path):
    sr.register(tmp_path, agent="qoder-cn", workspace=str(tmp_path), provider="uia", session_id="s1")
    sr.register(tmp_path, agent="qoder-cn", workspace=str(tmp_path), provider="uia", session_id="s2")
    result = sr.resolve(tmp_path, agent="qoder-cn", workspace=str(tmp_path))
    assert result["reason"] == "AMBIGUOUS_SESSION"
    result = sr.resolve(tmp_path, agent="qoder-cn", workspace=str(tmp_path), session_id="s1")
    assert result["ok"] and result["session"]["session_id"] == "s1"


def test_ack_lifecycle(tmp_path: Path):
    sr.register(tmp_path, agent="workbuddy", workspace=str(tmp_path), provider="uia", session_id="s")
    sr.transition(tmp_path, agent="workbuddy", session_id="s", state="delivered", event_id="e1")
    row = sr.transition(tmp_path, agent="workbuddy", session_id="s", state="acknowledged", event_id="e1")
    assert row["state"] == "acknowledged"


def test_bad_state(tmp_path: Path):
    sr.register(tmp_path, agent="a", workspace=str(tmp_path), provider="x", session_id="s")
    with pytest.raises(ValueError):
        sr.transition(tmp_path, agent="a", session_id="s", state="sent")


def test_existing_session_reregistration_preserves_identity_and_time(tmp_path: Path):
    first = sr.register(tmp_path, agent="qoder", workspace=str(tmp_path), provider="uia", session_id="s")
    second = sr.register(tmp_path, agent="qoder", workspace=str(tmp_path), provider="uia", session_id="s")
    assert second["registered_at"] == first["registered_at"]
    assert len(sr.list_sessions(tmp_path)) == 1


@pytest.mark.parametrize("changed", ["workspace", "provider", "agent", "session_id"])
def test_colliding_identity_never_overwrites_legacy_record(tmp_path: Path, changed: str):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = {"agent": "qoder__cn", "workspace": str(tmp_path / "a"), "provider": "uia", "session_id": "s"}
    sr.register(tmp_path, **first)
    row_path = next((tmp_path / "clients" / "sessions").glob("*.json"))
    original = row_path.read_bytes()
    second = dict(first)
    if changed == "workspace": second["workspace"] = str(tmp_path / "b")
    elif changed == "provider": second["provider"] = "cdp"
    elif changed == "agent": second.update(agent="qoder", session_id="cn__s")
    if changed == "session_id":
        # Slash and underscore map to the same legacy filename.
        sr.register(tmp_path, agent="other", workspace=str(tmp_path / "a"), provider="uia", session_id="x/y")
        row_path = next(p for p in (tmp_path / "clients" / "sessions").glob("*.json") if p.name.startswith("other__"))
        original = row_path.read_bytes()
        second = dict(first, agent="other", session_id="x_y")
    with pytest.raises(ValueError, match="SESSION_IDENTITY_CONFLICT"):
        sr.register(tmp_path, **second)
    assert row_path.read_bytes() == original


@pytest.mark.parametrize("content", ["{broken", "{}", "[]", '"text"'])
def test_corrupt_legacy_record_is_not_overwritten(tmp_path: Path, content: str):
    folder = tmp_path / "clients" / "sessions"
    folder.mkdir(parents=True)
    path = folder / "qoder__s.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="SESSION_RECORD_UNREADABLE"):
        sr.register(tmp_path, agent="qoder", workspace=str(tmp_path), provider="uia", session_id="s")
    assert path.read_text(encoding="utf-8") == content


def test_unrelated_corrupt_record_does_not_crash_resolution(tmp_path: Path):
    sr.register(tmp_path, agent="qoder", workspace=str(tmp_path), provider="uia", session_id="s")
    (tmp_path / "clients" / "sessions" / "corrupt.json").write_text("[]", encoding="utf-8")
    assert sr.resolve(tmp_path, agent="qoder")["ok"] is True


def test_transition_uses_persisted_case_not_caller_case(tmp_path: Path):
    sr.register(tmp_path, agent="Qoder", workspace=str(tmp_path), provider="uia", session_id="s")
    sr.transition(tmp_path, agent="qoder", session_id="s", state="delivered", event_id="e")
    assert len(list((tmp_path / "clients" / "sessions").glob("*.json"))) == 1
    assert sr.resolve(tmp_path, agent="Qoder")["session"]["state"] == "delivered"


def test_threads_cannot_overwrite_conflicting_identity(tmp_path: Path):
    def attempt(i):
        try:
            return sr.register(tmp_path, agent="qoder", workspace=str(tmp_path / str(i)), provider="uia", session_id="s")["workspace"]
        except ValueError as exc:
            assert str(exc) == "SESSION_IDENTITY_CONFLICT"
            return "conflict"
    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(attempt, range(8)))
    assert values.count("conflict") == 7
    assert len(sr.list_sessions(tmp_path)) == 1


def test_processes_cannot_overwrite_conflicting_identity(tmp_path: Path):
    code = (
        "import sys; from agent_evolution_protocol.coordination import session_registry as s; "
        "s.register(sys.argv[1],agent='qoder',workspace=sys.argv[2],provider='uia',session_id='s')"
    )
    env = {**os.environ, "PYTHONUTF8": "1"}
    children = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path), str(tmp_path / str(i))],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) for i in range(6)]
    results = [child.communicate(timeout=30) for child in children]
    assert sum(child.returncode == 0 for child in children) == 1
    assert all(child.returncode == 0 or b"SESSION_IDENTITY_CONFLICT" in err
               for child, (_, err) in zip(children, results))
    assert len(sr.list_sessions(tmp_path)) == 1
