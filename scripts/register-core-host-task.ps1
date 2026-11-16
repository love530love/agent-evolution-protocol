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
  [switch]$Register,
  [switch]$Start
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $CoordRoot) { $CoordRoot = Join-Path $repoRoot 'coordination' }
if (-not $BridgeRoot) { $BridgeRoot = $CoordRoot }
$coordRoot = [IO.Path]::GetFullPath($CoordRoot)
$bridgeRoot = [IO.Path]::GetFullPath($BridgeRoot)
$pwsh = (Get-Command pwsh.exe -ErrorAction Stop).Source
$launcher = Join-Path $repoRoot 'scripts\aep-core-host.ps1'
$taskLauncher = Join-Path $repoRoot 'scripts\run-aep-core-host.cmd'

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

$scheduledTasks = Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue
if ($scheduledTasks) {
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
} else {
  # PowerShell 7 installations frequently do not expose ScheduledTasks.  The
  # native schtasks client is present on supported Windows versions and avoids
  # silently falling back to Windows PowerShell just to register one task.
  # schtasks limits /TR to 261 characters.  The installed Codex PowerShell can
  # itself consume most of that budget, so use a short .cmd launcher.  It finds
  # PowerShell 7 at run time and forwards these bounded, quoted arguments.
  $run = '"' + $taskLauncher + '" -CoordRoot ' + $quotedCoord + ' -BridgeRoot ' + $quotedBridge + ' -IntervalSeconds ' + [Math]::Max(5, $IntervalSeconds)
  if ($run.Length -gt 261) { throw "scheduled task command exceeds schtasks /TR limit ($($run.Length) > 261); shorten the repository path" }
  $args = @('/Create', '/TN', $TaskName, '/TR', $run, '/F')
  if ($RunAsSystem) {
    $args += @('/SC', 'ONSTART', '/RU', 'SYSTEM', '/RL', 'HIGHEST')
  } else {
    $args += @('/SC', 'ONLOGON', '/RL', 'LIMITED')
  }
  & schtasks.exe @args
  if ($LASTEXITCODE -ne 0) { throw "schtasks registration failed with exit code $LASTEXITCODE" }
}
if ($Start) {
  & schtasks.exe /Run /TN $TaskName
  if ($LASTEXITCODE -ne 0) { throw "schtasks start failed with exit code $LASTEXITCODE" }
}
Write-Output "Registered $TaskName. Verify with: Get-ScheduledTask -TaskName '$TaskName' and inspect $coordRoot\runtime\aep-core-host.heartbeat.json"
