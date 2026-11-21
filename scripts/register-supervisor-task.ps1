param(
  [string]$TaskName = "AEP-DesktopHost",
  [int]$IntervalMinutes = 1,
  [string]$CoordRoot = "",
  [switch]$ObserveOnly,
  [switch]$Start
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
$python = Join-Path $root '.tools\agent-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  throw "AEP runtime missing: $python. Install the project runtime before registering the supervisor."
}
$coordRoot = if ($CoordRoot) {
  [IO.Path]::GetFullPath($CoordRoot)
} else {
  Join-Path $root 'coordination'
}
$pwsh = (Get-Command pwsh.exe -ErrorAction Stop).Source
$launcher = Join-Path $root 'scripts\aep-supervisor.ps1'
$taskLauncher = Join-Path $root 'scripts\run-aep-supervisor.cmd'
$argument = "-NoLogo -NoProfile -NonInteractive -File `"$launcher`" -CoordRoot `"$coordRoot`" -IntervalSeconds $([Math]::Max(5, $IntervalMinutes * 60))"
if ($ObserveOnly) { $argument += " -ObserveOnly" }
$mode = if ($ObserveOnly) { "observe-only" } else { "dispatch" }
$scheduledTasks = Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue
if ($scheduledTasks) {
  $action = New-ScheduledTaskAction -Execute $pwsh -Argument $argument -WorkingDirectory $root
  $trigger = New-ScheduledTaskTrigger -AtLogOn
  $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 23) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "AEP interactive desktop host ($mode); single instance, bounded coordination loop" -Force | Out-Null
} else {
  $run = '"' + $taskLauncher + '" -CoordRoot "' + $coordRoot + '" -IntervalSeconds ' + [Math]::Max(5, $IntervalMinutes * 60)
  if ($ObserveOnly) { $run += ' -ObserveOnly' }
  if ($run.Length -gt 261) { throw "scheduled task command exceeds schtasks /TR limit ($($run.Length) > 261); shorten the repository path" }
  & schtasks.exe /Create /TN $TaskName /TR $run /SC ONLOGON /RL LIMITED /F
  if ($LASTEXITCODE -ne 0) { throw "schtasks registration failed with exit code $LASTEXITCODE" }
}
if ($Start) {
  & schtasks.exe /Run /TN $TaskName
  if ($LASTEXITCODE -ne 0) { throw "schtasks start failed with exit code $LASTEXITCODE" }
}
Write-Output "Registered $TaskName in $mode mode for coordination root $coordRoot. Remove with: Unregister-ScheduledTask -TaskName '$TaskName'"
