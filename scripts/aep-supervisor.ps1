param([int]$IntervalSeconds = 30, [int]$MaxTicks = 0)
$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$OutputEncoding = [Console]::OutputEncoding
$mutex = New-Object Threading.Mutex($false, "Global\AEP-Supervisor")
if (-not $mutex.WaitOne(0)) { Write-Output "AEP supervisor already running; exiting duplicate instance."; exit 0 }
$root = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
$ticks = 0
while (($MaxTicks -eq 0) -or ($ticks -lt $MaxTicks)) {
  & aep coord-commander tick --limit 20 2>&1 | Out-File -Append -Encoding utf8 (Join-Path $root "coordination\supervisor.log")
  $ticks++
  Start-Sleep -Seconds ([Math]::Max(5, $IntervalSeconds))
}
$mutex.ReleaseMutex(); $mutex.Dispose()
