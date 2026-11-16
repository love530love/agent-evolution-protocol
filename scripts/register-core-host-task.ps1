#Requires -Version 7.0
<#!
.SYNOPSIS
Registers the non-interactive AEP Core Host as a single long-lived task.

.DESCRIPTION
The default task runs at the current user's logon.  This is deliberate: a
mapped project drive is often invisible to SYSTEM, and a task that starts but
cannot import AEP is not a dependable control plane.  Use -RunAsSystem only
when the repository path is on a local fixed drive and an Administrator is
performing the deployment.  Core Host never performs GUI actions in either
mode; real desktop work remains the per-user AEP-DesktopHost task.
#>
param(
  [string]$TaskName = 'AEP-CoreHost',
  [int]$IntervalSeconds = 30,
  [string]$CoordRoot = '',
  [string]$BridgeRoot = '',
  [switch]$RunAsSystem,
  [switch]$Register
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $CoordRoot) { $CoordRoot = Join-Path $repoRoot 'coordination' }
if (-not $BridgeRoot) { $BridgeRoot = $CoordRoot }
$coordRoot = [IO.Path]::GetFullPath($CoordRoot)
$bridgeRoot = [IO.Path]::GetFullPath($BridgeRoot)
$pwsh = (Get-Command pwsh.exe -ErrorAction Stop).Source
$launcher = Join-Path $repoRoot 'scripts\aep-core-host.ps1'

if ($RunAsSystem) {
  $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($repoRoot))
  if ($drive.DriveType -ne [IO.DriveType]::Fixed) {
    throw "-RunAsSystem requires a local fixed repository drive; $repoRoot is $($drive.DriveType). Use the default logon task or relocate the repository."
  }
}

$quotedLauncher = '"' + $launcher.Replace('"', '`"') + '"'
$quotedCoord = '"' + $coordRoot.Replace('"', '`"') + '"'
$quotedBridge = '"' + $bridgeRoot.Replace('"', '`"') + '"'
$taskArguments = "-NoLogo -NoProfile -NonInteractive -File $quotedLauncher -CoordRoot $quotedCoord -BridgeRoot $quotedBridge -IntervalSeconds $([Math]::Max(5, $IntervalSeconds))"
$plan = [ordered]@{
  task_name = $TaskName
  run_as = if ($RunAsSystem) { 'SYSTEM' } else { 'CURRENT_USER_AT_LOGON' }
  executable = $pwsh
  arguments = $taskArguments
  working_directory = $repoRoot
  touch_desktop = $false
  registration_requested = [bool]$Register
}

if (-not $Register) {
  $plan | ConvertTo-Json -Depth 4
  Write-Output 'Preview only. Add -Register to create or replace this task.'
  exit 0
}

$action = New-ScheduledTaskAction -Execute $pwsh -Argument $taskArguments -WorkingDirectory $repoRoot
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 23) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
if ($RunAsSystem) {
  $trigger = New-ScheduledTaskTrigger -AtStartup
  $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
} else {
  $trigger = New-ScheduledTaskTrigger -AtLogOn
  $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'AEP Core Host: durable coordination only; never desktop automation' -Force | Out-Null
Write-Output "Registered $TaskName. Verify with: Get-ScheduledTask -TaskName '$TaskName' and inspect $coordRoot\runtime\aep-core-host.heartbeat.json"
