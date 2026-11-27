"""Contracts for the Windows interactive desktop-host task scripts."""

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_supervisor_guardian_and_registration_default_to_the_real_repository_coordination_root():
    supervisor = (REPO / "scripts" / "aep-supervisor.ps1").read_text(encoding="utf-8")
    guardian = (REPO / "scripts" / "aep-desktop-guardian.ps1").read_text(encoding="utf-8")
    registration = (REPO / "scripts" / "register-supervisor-task.ps1").read_text(encoding="utf-8")
    assert "Join-Path $repoRoot 'coordination'" in supervisor
    assert "Join-Path $repoRoot 'coordination'" in guardian
    assert "Join-Path $root 'coordination'" in registration
    assert ".agent-evolution\\coordination" not in supervisor
    assert ".agent-evolution\\coordination" not in registration


def test_registration_runs_a_direct_base_python_guardian_in_the_interactive_logon_session():
    registration = (REPO / "scripts" / "register-supervisor-task.ps1").read_text(encoding="utf-8")
    runner = (REPO / "scripts" / "aep-desktop-guardian-runner.py").read_text(encoding="utf-8")
    for token in (
        "AEP-DesktopHost", "[string]$CoordRoot", "_base_executable",
        "scripts\\aep-desktop-guardian-runner.py", "--coord-root", "--interval-seconds",
        "New-ScheduledTaskTrigger -AtLogOn", "MultipleInstances IgnoreNew",
        "ExecutionTimeLimit", "AEP Desktop Guardian", "[switch]$ObserveOnly",
        "--observe-only", "[switch]$Start", "Get-Command Register-ScheduledTask",
        "schtasks.exe", "$cmd = Escape-AepXml $basePython", "<Hidden>true</Hidden>",
        "-RestartCount 3", "-RestartInterval (New-TimeSpan -Minutes 1)",
    ):
        assert token in registration
    for token in ("site.addsitedir", "agent_evolution_protocol.coordination.resident_guardian",
                  "agent-runtime", "raise SystemExit(main())"):
        assert token in runner
