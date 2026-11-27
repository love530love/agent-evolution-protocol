param(
  [string]$TaskName = "AEP-DesktopHost",
  [int]$IntervalMinutes = 1,
  [string]$CoordRoot = "",
  [switch]$ObserveOnly,
  [switch]$LocalQueue,
  # 0 = no execution time limit (PT0S).  A positive value is the explicit bound
  # in hours.  The previous hard-coded 23h cut a long-lived host off before the
  # next overnight watch window, so the default must never truncate.
  [ValidateRange(0, 876000)][int]$ExecutionTimeLimitHours = 0,
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
# Prefer the stable system installation over a host application's bundled
# version, whose versioned directory can disappear on the next app update.
$systemPwsh = if ($env:ProgramFiles) { Join-Path $env:ProgramFiles 'PowerShell\7\pwsh.exe' } else { '' }
$pwsh = if ($systemPwsh -and (Test-Path -LiteralPath $systemPwsh -PathType Leaf)) {
  $systemPwsh
} else {
  (Get-Command pwsh.exe -ErrorAction Stop).Source
}
$launcher = Join-Path $root 'scripts\aep-supervisor.ps1'
$argument = "-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -File `"$launcher`" -CoordRoot `"$coordRoot`" -IntervalSeconds $([Math]::Max(5, $IntervalMinutes * 60))"
if ($ObserveOnly) { $argument += " -ObserveOnly" }
if ($LocalQueue -and -not $ObserveOnly) { $argument += " -LocalQueue" }
$mode = if ($ObserveOnly) { "observe-only" } else { "dispatch" }
$executionLimit = if ($ExecutionTimeLimitHours -gt 0) { New-TimeSpan -Hours $ExecutionTimeLimitHours } else { [TimeSpan]::Zero }
$executionLimitXml = if ($ExecutionTimeLimitHours -gt 0) { "PT$($ExecutionTimeLimitHours)H" } else { 'PT0S' }
$scheduledTasks = Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue
if ($scheduledTasks) {
  $action = New-ScheduledTaskAction -Execute $pwsh -Argument $argument -WorkingDirectory $root
  $trigger = New-ScheduledTaskTrigger -AtLogOn
  $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit $executionLimit -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "AEP interactive desktop host ($mode); single instance, bounded coordination loop" -Force | Out-Null
} else {
  # schtasks.exe has no command-line switch for ExecutionTimeLimit or RestartOnFailure.
  # Register from a generated task XML so this fallback carries the SAME real
  # recovery semantics as the module branch instead of silently claiming success.
  # <RunLevel>LeastPrivilege is the XML equivalent of the former /RL LIMITED.
  function Escape-AepXml([string]$value) {
    $value.Replace('&','&amp;').Replace('<','&lt;').Replace('>','&gt;').Replace('"','&quot;')
  }
  $userId = Escape-AepXml "$env:USERDOMAIN\$env:USERNAME"
  # Use pwsh directly with its hidden-window flag.  A .cmd launcher would
  # create a visible console for an interactive logon before starting pwsh.
  $cmd = Escape-AepXml $pwsh
  $arguments = Escape-AepXml $argument
  $workDir = Escape-AepXml $root
  $taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>AEP interactive desktop host ($mode); single instance, bounded coordination loop</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$userId</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>$executionLimitXml</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$cmd</Command>
      <Arguments>$arguments</Arguments>
      <WorkingDirectory>$workDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
  $xmlPath = Join-Path $env:TEMP ("aep-supervisor-{0}.xml" -f [guid]::NewGuid().ToString('N'))
  try {
    [IO.File]::WriteAllText($xmlPath, $taskXml, [Text.UnicodeEncoding]::new($false, $true))
    & schtasks.exe /Create /TN $TaskName /XML $xmlPath /F
    if ($LASTEXITCODE -ne 0) { throw "schtasks registration failed with exit code $LASTEXITCODE" }
  } finally {
    Remove-Item -LiteralPath $xmlPath -Force -ErrorAction SilentlyContinue
  }
}
if ($Start) {
  & schtasks.exe /Run /TN $TaskName
  if ($LASTEXITCODE -ne 0) { throw "schtasks start failed with exit code $LASTEXITCODE" }
}
Write-Output "Registered $TaskName in $mode mode for coordination root $coordRoot. Remove with: Unregister-ScheduledTask -TaskName '$TaskName'"
