param(
  [string]$TaskName = "AEP-DesktopHost",
  [int]$IntervalMinutes = 1,
  [string]$CoordRoot = ""
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
$argument = "-NoLogo -NoProfile -NonInteractive -File `"$launcher`" -CoordRoot `"$coordRoot`" -IntervalSeconds $([Math]::Max(5, $IntervalMinutes * 60))"
$action = New-ScheduledTaskAction -Execute $pwsh -Argument $argument -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 23) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "AEP interactive desktop host; single instance, bounded coordination loop" -Force
Write-Output "Registered $TaskName for coordination root $coordRoot. Remove with: Unregister-ScheduledTask -TaskName '$TaskName'"
