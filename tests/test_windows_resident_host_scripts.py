"""Contracts for the Windows interactive desktop-host task scripts."""

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_supervisor_and_registration_default_to_the_real_repository_coordination_root():
    supervisor = (REPO / "scripts" / "aep-supervisor.ps1").read_text(encoding="utf-8")
    registration = (REPO / "scripts" / "register-supervisor-task.ps1").read_text(encoding="utf-8")
    assert "Join-Path $repoRoot 'coordination'" in supervisor
    assert "Join-Path $root 'coordination'" in registration
    assert ".agent-evolution\\coordination" not in supervisor
    assert ".agent-evolution\\coordination" not in registration


def test_registration_runs_the_pwsh_wrapper_in_the_interactive_logon_session():
    registration = (REPO / "scripts" / "register-supervisor-task.ps1").read_text(encoding="utf-8")
    for token in (
        "AEP-DesktopHost", "[string]$CoordRoot", "Get-Command pwsh.exe",
        "scripts\\aep-supervisor.ps1", "-CoordRoot", "-IntervalSeconds",
        "New-ScheduledTaskTrigger -AtLogOn", "MultipleInstances IgnoreNew",
        "ExecutionTimeLimit", "AEP interactive desktop host", "[switch]$ObserveOnly",
        "-ObserveOnly", "[switch]$Start", "Get-Command Register-ScheduledTask",
        "schtasks.exe", "run-aep-supervisor.cmd",
        "-RestartCount 3", "-RestartInterval (New-TimeSpan -Minutes 1)",
    ):
        assert token in registration
