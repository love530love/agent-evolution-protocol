param([string]$TaskName = "AEP-Supervisor", [int]$IntervalMinutes = 1)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
$python = Join-Path $root '.tools\agent-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  throw "AEP runtime missing: $python. Install the project runtime before registering the supervisor."
}
$coordRoot = Join-Path $root '.agent-evolution\coordination'
$argument = "-m agent_evolution_protocol.coordination.supervisor --coord-root `"$coordRoot`" --interval-seconds $([Math]::Max(5, $IntervalMinutes * 60))"
$action = New-ScheduledTaskAction -Execute $python -Argument $argument -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 23) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "AEP Python supervisor; single instance, bounded command loop" -Force
Write-Output "Registered $TaskName using the project Python runtime. Remove with: Unregister-ScheduledTask -TaskName '$TaskName'"
