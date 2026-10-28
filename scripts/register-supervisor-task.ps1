param([string]$TaskName = "AEP-Supervisor", [int]$IntervalMinutes = 1)
$root = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
$script = Join-Path $root "scripts\aep-supervisor.ps1"
$action = New-ScheduledTaskAction -Execute "pwsh.exe" -Argument "-NoLogo -NoProfile -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Description "AEP bounded supervisor; monitors existing tasks only" -Force
Write-Output "Registered $TaskName. Remove with: Unregister-ScheduledTask -TaskName '$TaskName'"
