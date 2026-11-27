param(
  [string]$TaskName = "AEP-DesktopHost",
  [int]$IntervalMinutes = 1,
  [string]$CoordRoot = "",
  [string]$RuntimePython = "",
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
$runtimePython = if ($RuntimePython) { [IO.Path]::GetFullPath($RuntimePython) } else { Join-Path $root '.tools\agent-runtime\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
  throw "AEP runtime missing: $runtimePython. Install the project runtime before registering the supervisor."
}
$basePython = ((& $runtimePython -c "import sys; print(getattr(sys, '_base_executable', sys.executable))") | Select-Object -First 1).Trim()
if (-not $basePython -or -not (Test-Path -LiteralPath $basePython -PathType Leaf)) {
  throw "AEP base interpreter unavailable from runtime: $runtimePython"
}
$coordRoot = if ($CoordRoot) {
  [IO.Path]::GetFullPath($CoordRoot)
} else {
  Join-Path $root 'coordination'
}
$runner = Join-Path $root 'scripts\aep-desktop-guardian-runner.py'
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { throw "AEP Guardian runner missing: $runner" }
# Start the actual base Python interpreter directly.  The runtime's venv
# redirector can otherwise outlive the Scheduler action and leave Guardian
# orphaned when a task is stopped or replaced.
$argument = "`"$runner`" --coord-root `"$coordRoot`" --interval-seconds $([Math]::Max(5, $IntervalMinutes * 60))"
if ($ObserveOnly) { $argument += " --observe-only" }
if ($LocalQueue -and -not $ObserveOnly) { $argument += " --local-queue" }
$mode = if ($ObserveOnly) { "observe-only" } else { "dispatch" }
$executionLimit = if ($ExecutionTimeLimitHours -gt 0) { New-TimeSpan -Hours $ExecutionTimeLimitHours } else { [TimeSpan]::Zero }
$executionLimitXml = if ($ExecutionTimeLimitHours -gt 0) { "PT$($ExecutionTimeLimitHours)H" } else { 'PT0S' }
$scheduledTasks = Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue
if ($scheduledTasks) {
  $action = New-ScheduledTaskAction -Execute $basePython -Argument $argument -WorkingDirectory $root
  $trigger = New-ScheduledTaskTrigger -AtLogOn
  $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit $executionLimit -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "AEP Desktop Guardian ($mode); owns one bounded interactive host child" -Force | Out-Null
} else {
  # schtasks.exe has no command-line switch for ExecutionTimeLimit or RestartOnFailure.
  # Register from a generated task XML so this fallback carries the SAME real
  # recovery semantics as the module branch instead of silently claiming success.
  # <RunLevel>LeastPrivilege is the XML equivalent of the former /RL LIMITED.
  function Escape-AepXml([string]$value) {
    $value.Replace('&','&amp;').Replace('<','&lt;').Replace('>','&gt;').Replace('"','&quot;')
  }
  $userId = Escape-AepXml "$env:USERDOMAIN\$env:USERNAME"
  # Launch the actual base interpreter, not the runtime's venv redirector.
  # A direct process is essential: ending or replacing the task must end the
  # Guardian, which in turn closes its Job and reaps only its owned Host tree.
  $cmd = Escape-AepXml $basePython
  $arguments = Escape-AepXml $argument
  $workDir = Escape-AepXml $root
  $taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>AEP Desktop Guardian ($mode); owns one bounded interactive host child</Description></RegistrationInfo>
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
    <Hidden>true</Hidden>
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
