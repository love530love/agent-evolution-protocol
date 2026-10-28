param([int]$IntervalSeconds = 30, [int]$MaxTicks = 0)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
$ticks = 0
while (($MaxTicks -eq 0) -or ($ticks -lt $MaxTicks)) {
  & aep coord-commander tick --limit 20 2>&1 | Out-File -Append -Encoding utf8 (Join-Path $root "coordination\supervisor.log")
  $ticks++
  Start-Sleep -Seconds ([Math]::Max(5, $IntervalSeconds))
}
