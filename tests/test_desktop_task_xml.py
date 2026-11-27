"""Verify the desktop task registrar without installing a real task."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest


@pytest.mark.skipif(os.name != "nt" or not shutil.which("pwsh"), reason="Windows PowerShell required")
def test_fallback_registers_hidden_observe_only_task_with_recovery(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    xml_path = tmp_path / "task.xml"
    project = tmp_path / "isolated project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    registrar = scripts / "register-supervisor-task.ps1"
    shutil.copy(repo / "scripts" / "register-supervisor-task.ps1", registrar)
    shutil.copy(repo / "scripts" / "aep-desktop-guardian-runner.py",
                scripts / "aep-desktop-guardian-runner.py")
    # The isolated tree intentionally has no machine-specific .tools runtime.
    # Give the registrar a real Python only to derive its base interpreter; the
    # generated task does not execute during this XML-only test.
    runtime = Path(sys.executable)
    env = dict(os.environ, AEP_TEST_REGISTRAR=str(registrar),
               AEP_TEST_TASK_XML=str(xml_path), AEP_TEST_RUNTIME=str(runtime))
    harness = r'''
function Get-Command {
  param([string]$Name, [string]$ErrorAction)
  if ($Name -eq 'Register-ScheduledTask') { return $null }
  Microsoft.PowerShell.Core\Get-Command -Name $Name -ErrorAction Stop
}
function schtasks.exe {
  $index = [array]::IndexOf($args, '/XML')
  if ($index -lt 0) { throw 'expected XML task registration' }
  Copy-Item -LiteralPath $args[$index + 1] -Destination $env:AEP_TEST_TASK_XML
  $global:LASTEXITCODE = 0
}
& $env:AEP_TEST_REGISTRAR -TaskName 'AEP-Test-Only' -ObserveOnly -RuntimePython $env:AEP_TEST_RUNTIME
'''
    result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", harness],
                            env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    root = ET.parse(xml_path).getroot()
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find("t:Settings/t:ExecutionTimeLimit", ns).text == "PT0S"
    assert root.find("t:Settings/t:RestartOnFailure/t:Count", ns).text == "3"
    assert root.find("t:Settings/t:MultipleInstancesPolicy", ns).text == "IgnoreNew"
    command = root.find("t:Actions/t:Exec/t:Command", ns).text
    assert command.lower().endswith("python.exe")
    assert os.path.normcase(command) != os.path.normcase(str(shutil.which("pwsh")))
    arguments = root.find("t:Actions/t:Exec/t:Arguments", ns).text
    assert "aep-desktop-guardian-runner.py" in arguments
    assert "--observe-only" in arguments
    assert root.find("t:Settings/t:Hidden", ns).text == "true"
