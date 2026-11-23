import json
import os
import subprocess
import sys
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_evolution_protocol.coordination import workspace_binding as binding
from agent_evolution_protocol.federation import engine
from agent_evolution_protocol.federation.store import workspace_dir


def workspace(root, workspace_id):
    path = workspace_dir(root, workspace_id) / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": engine.STATE_SCHEMA, "workspace_id": workspace_id}), encoding="utf-8")


def test_existing_workspace_binds_idempotently_and_readonly_survives_reload(tmp_path):
    coord, federation, project = tmp_path / "coord#%", tmp_path / "federation", tmp_path / "项目"
    federation.mkdir()
    project.mkdir()
    ident = "W-" + "a" * 16
    workspace(federation, ident)
    assert binding.bind(coord, federation, project, ident)["created"] is True
    assert binding.bind(coord, federation, project, ident)["created"] is False
    row = binding.lookup(coord, project)
    assert row["workspace_id"] == ident
    assert row["identity_verified"] is False
    assert row["session_verified"] is False
    assert row["workspace_state_validated"] is True
    assert row["project_association_verified"] is False
    assert not (tmp_path / "coord").exists()


def test_missing_and_conflicting_workspaces_never_create_or_remap(tmp_path):
    coord, federation, project, other = (tmp_path / name for name in ("coord", "federation", "project", "other"))
    for directory in (federation, project, other):
        directory.mkdir()
    ident = "W-" + "a" * 16
    with pytest.raises(ValueError, match="workspace state not found"):
        binding.bind(coord, federation, project, ident)
    assert not coord.exists()
    workspace(federation, ident)
    binding.bind(coord, federation, project, ident)
    with pytest.raises(ValueError, match="WORKSPACE_BINDING_CONFLICT"):
        binding.bind(coord, federation, other, ident)
    assert binding.lookup(coord, project)["workspace_id"] == ident
    with pytest.raises(ValueError, match="BINDING_NOT_FOUND"):
        binding.lookup(coord, other)
    (workspace_dir(federation, ident) / "state.json").unlink()
    with pytest.raises(ValueError, match="workspace state not found"):
        binding.lookup(coord, project)


def test_multiple_workspaces_require_explicit_selection(tmp_path):
    coord, federation, project = tmp_path / "coord", tmp_path / "federation", tmp_path / "project"
    federation.mkdir()
    project.mkdir()
    for ident in ("W-" + "a" * 16, "W-" + "b" * 16):
        workspace(federation, ident)
        binding.bind(coord, federation, project, ident)
    with pytest.raises(ValueError, match="AMBIGUOUS_BINDING"):
        binding.lookup(coord, project)
    assert binding.lookup(coord, project, "W-" + "b" * 16)["workspace_id"] == "W-" + "b" * 16


def test_concurrent_repeated_binding_is_idempotent(tmp_path):
    coord, federation, project = tmp_path / "coord", tmp_path / "federation", tmp_path / "project"
    federation.mkdir()
    project.mkdir()
    ident = "W-" + "a" * 16
    workspace(federation, ident)
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(lambda _: binding.bind(coord, federation, project, ident), range(16)))
    assert sum(row["created"] for row in result) == 1


def test_cli_concurrent_processes_share_single_binding(tmp_path):
    coord, federation, project = tmp_path / "coord", tmp_path / "federation", tmp_path / "project"
    federation.mkdir()
    project.mkdir()
    ident = "W-" + "a" * 16
    workspace(federation, ident)
    command = [sys.executable, "-m", "agent_evolution_protocol.coordination.workspace_binding",
               "bind", "--coord-root", str(coord), "--federation-root", str(federation),
               "--project-root", str(project), "--workspace-id", ident]
    env = {**os.environ, "PYTHONUTF8": "1"}
    children = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, encoding="utf-8", env=env,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) for _ in range(8)]
    outputs = []
    for child in children:
        output, error = child.communicate(timeout=30)
        assert child.returncode == 0, error
        outputs.append(json.loads(output))
    assert sum(output["created"] for output in outputs) == 1


@pytest.mark.parametrize("errors,expected_calls", [
    (["database is locked", "attempt to write a readonly database"], 3),
    (["database is locked"] * 5, 5),
    (["disk I/O error"], 1),
])
def test_initialization_retries_only_bounded_transient_sqlite_failures(tmp_path, monkeypatch, errors, expected_calls):
    coord, federation, project = tmp_path / "coord", tmp_path / "federation", tmp_path / "project"
    federation.mkdir()
    project.mkdir()
    ident = "W-" + "a" * 16
    workspace(federation, ident)
    real_connect = sqlite3.connect
    remaining = list(errors)
    calls = []

    class Proxy:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, *args):
            if statement == binding._SCHEMA and remaining:
                raise sqlite3.OperationalError(remaining.pop(0))
            return self.connection.execute(statement, *args)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    def failing_connect(*args, **kwargs):
        calls.append(1)
        return Proxy(real_connect(*args, **kwargs))

    monkeypatch.setattr(binding.sqlite3, "connect", failing_connect)
    monkeypatch.setattr(binding.time, "sleep", lambda _: None)
    if expected_calls == 3:
        assert binding.bind(coord, federation, project, ident)["created"] is True
    else:
        with pytest.raises(sqlite3.OperationalError):
            binding.bind(coord, federation, project, ident)
    assert len(calls) == expected_calls
