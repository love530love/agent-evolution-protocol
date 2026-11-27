#Requires -Version 7.0
param(
  [int]$IntervalSeconds = 5,
  [int]$MaxTicks = 0,
  [int]$StaleAfterSeconds = 90,
  [int]$StartupGraceSeconds = 30,
  [int]$MaxRestarts = 5,
  [int]$RestartWindowSeconds = 600,
  [string]$CoordRoot = '',
  [switch]$NoPerceive,
  [switch]$LocalQueue,
  [switch]$ObserveOnly
)

$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $CoordRoot) { $CoordRoot = Join-Path $repoRoot 'coordination' }
$CoordRoot = [IO.Path]::GetFullPath($CoordRoot)

# The long-lived parent lives in Python so it can keep one durable restart
# ledger and one explicit child handle.  This PowerShell file is only a hidden
# Task Scheduler/UAC compatibility entry point; it does not poll, start an
# extra per-tick child, or inject desktop input by itself.
$runtimePython = Join-Path $repoRoot '.tools\agent-runtime\Scripts\python.exe'
if (Test-Path -LiteralPath $runtimePython -PathType Leaf) {
  $basePython = ((& $runtimePython -c "import sys; print(getattr(sys, '_base_executable', sys.executable))") | Select-Object -First 1).Trim()
  if (-not $basePython -or -not (Test-Path -LiteralPath $basePython -PathType Leaf)) {
    throw "AEP base interpreter unavailable from runtime: $runtimePython"
  }
} else {
  $basePython = (Get-Command python.exe -ErrorAction Stop).Source
}
$runner = Join-Path $repoRoot 'scripts\aep-desktop-guardian-runner.py'
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { throw "AEP Guardian runner missing: $runner" }
$arguments = @($runner,
               '--coord-root', $CoordRoot,
               '--interval-seconds', [string][Math]::Max(1, $IntervalSeconds),
               '--stale-after-seconds', [string][Math]::Max(1, $StaleAfterSeconds),
               '--startup-grace-seconds', [string][Math]::Max(1, $StartupGraceSeconds),
               '--max-restarts', [string][Math]::Max(1, $MaxRestarts),
               '--restart-window-seconds', [string][Math]::Max(1, $RestartWindowSeconds),
               '--max-ticks', [string][Math]::Max(0, $MaxTicks))
if ($NoPerceive) { $arguments += '--no-perceive' }
if ($LocalQueue) { $arguments += '--local-queue' }
if ($ObserveOnly) { $arguments += '--observe-only' }
& $basePython @arguments
exit $LASTEXITCODE
