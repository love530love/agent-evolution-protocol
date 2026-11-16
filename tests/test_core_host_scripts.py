from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_core_host_wrapper_starts_python_module_without_a_per_tick_child_process():
    source = (ROOT / "scripts" / "aep-core-host.ps1").read_text(encoding="utf-8")
    assert "#Requires -Version 7.0" in source
    assert "agent_evolution_protocol.coordination.core_host" in source
    assert "Get-Command pwsh.exe" not in source
    assert "Start-Process" not in source


def test_core_host_registration_defaults_to_user_logon_and_gates_system_mode():
    source = (ROOT / "scripts" / "register-core-host-task.ps1").read_text(encoding="utf-8")
    assert "[switch]$RunAsSystem" in source
    assert "CURRENT_USER_AT_LOGON" in source
    assert "New-ScheduledTaskTrigger -AtLogOn" in source
    assert "New-ScheduledTaskTrigger -AtStartup" in source
    assert "DriveType -ne [IO.DriveType]::Fixed" in source
    assert "AEP Core Host: durable coordination only; never desktop automation" in source
