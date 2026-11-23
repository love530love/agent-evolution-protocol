from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest

from agent_evolution_protocol.coordination import workspace_binding, workspace_join, workspace_members
from agent_evolution_protocol.federation import engine


def setup_binding(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    federation = tmp_path / "federation"
    federation.mkdir()
    coord = project / "coordination"
    workspace = engine.create(federation, goal="test", acceptance=["done"])
    ident = workspace["workspace_id"]
    workspace_binding.bind(coord, federation, project, ident)
    return project, federation, coord, ident


def test_join_requires_bound_workspace_and_is_idempotent(tmp_path):
    project, federation, coord, ident = setup_binding(tmp_path)
    first = workspace_join.join(coord, project, workspace_id=ident, agent="codex", session_id="s1")
    second = workspace_join.join(coord, project, workspace_id=ident, agent="codex", session_id="s1")
    assert first["membership"]["created"] is True
    assert second["membership"]["created"] is False
    assert workspace_members.notifications(coord, workspace_id=ident)["count"] == 1
    assert first["binding"]["project_association_verified"] is False
    assert first["presence_signalled"] is False
    assert first["notification_delivered"] is False
    assert first["agent_ack"] is False
    assert not (coord / "presence").exists()


def test_missing_or_wrong_binding_never_records_membership(tmp_path):
    project, federation, coord, ident = setup_binding(tmp_path)
    with pytest.raises(ValueError, match="WORKSPACE_ID_REQUIRED"):
        workspace_join.join(coord, project, workspace_id="", agent="codex")
    other = "W-" + "a" * 16
    if other == ident:
        other = "W-" + "b" * 16
    with pytest.raises(ValueError, match="BINDING_NOT_FOUND"):
        workspace_join.join(coord, project, workspace_id=other, agent="codex")
    assert not (coord / workspace_members.DB_NAME).exists()


def test_missing_federation_state_never_records_membership(tmp_path):
    project, federation, coord, ident = setup_binding(tmp_path)
    engine._state_path(federation, ident).unlink()
    with pytest.raises(ValueError, match="workspace state not found"):
        workspace_join.join(coord, project, workspace_id=ident, agent="codex")
    assert not (coord / workspace_members.DB_NAME).exists()


@pytest.mark.skipif(os.name != "nt" or not shutil.which("pwsh"), reason="Windows PowerShell integration")
def test_powershell_join_existing_binding_and_reject_invalid_before_presence(tmp_path):
    project, federation, coord, ident = setup_binding(tmp_path)
    script_dir = project / "scripts"
    script_dir.mkdir()
    source_root = Path(__file__).resolve().parents[1]
    shutil.copy(source_root / "scripts" / "join-agent.ps1", script_dir / "join-agent.ps1")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(source_root / "src"))
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    command = ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script_dir / "join-agent.ps1"),
               "-Agent", "codex", "-SessionId", "s1", "-WorkspaceId"]
    bad = subprocess.run(command + ["W-" + "b" * 16], env=env, capture_output=True, text=True, timeout=30)
    assert bad.returncode != 0
    assert not (coord / workspace_members.DB_NAME).exists()
    assert not (coord / "presence").exists()
    good = subprocess.run(command + [ident], env=env, capture_output=True, text=True, timeout=30)
    assert good.returncode == 0, good.stdout + good.stderr
    assert workspace_members.members(coord, workspace_id=ident)["count"] == 1
    no_session = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File",
                                 str(script_dir / "join-agent.ps1"), "-Agent", "qoder",
                                 "-WorkspaceId", ident], env=env, capture_output=True,
                                text=True, timeout=30)
    assert no_session.returncode == 0, no_session.stdout + no_session.stderr
    assert workspace_members.members(coord, workspace_id=ident)["count"] == 2


@pytest.mark.skipif(os.name != "nt" or not shutil.which("pwsh"), reason="Windows PowerShell integration")
def test_powershell_legacy_join_does_not_create_membership(tmp_path):
    project = tmp_path / "project"
    script_dir = project / "scripts"
    script_dir.mkdir(parents=True)
    source_root = Path(__file__).resolve().parents[1]
    shutil.copy(source_root / "scripts" / "join-agent.ps1", script_dir / "join-agent.ps1")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(source_root / "src"))
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File",
                             str(script_dir / "join-agent.ps1"), "-Agent", "codex",
                             "-SessionId", "s1"], env=env, capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (project / "coordination" / workspace_members.DB_NAME).exists()
